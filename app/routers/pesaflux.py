@router.post("/initiate", response_model=InitiateStkResponse)
async def initiate_stk_push(
    request_data: InitiateStkRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Initiate a PesaFlux M-Pesa STK Push for a plan purchase, upgrade, or recharge.

    - Plan price is loaded from the database if plan_id is provided.
    - If plan_id is missing, it treats it as a pure recharge using request_data.amount.
    - A unique reference is generated for every payment attempt.
    - The STK Push is sent to the user's phone.
    - Returns the reference for status polling.
    """
    plan = None
    amount_usd = 0.0
    payment_type = "purchase"
    plan_name = "Account Recharge"

    # Case A: Plan-based purchase/upgrade
    if request_data.plan_id:
        result = await db.execute(
            select(models.Plan).filter(
                models.Plan.id == request_data.plan_id,
                models.Plan.is_active == True  # noqa: E712
            )
        )
        plan = result.scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plan not found or is no longer available."
            )

        if plan.price == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The Intern (Free Trial) plan does not require payment."
            )

        if _plan_is_active(current_user):
            result_current = await db.execute(
                select(models.Plan).filter(models.Plan.id == current_user.current_plan_id)
            )
            current_plan = result_current.scalar_one_or_none()
            if current_plan and plan.price <= current_plan.price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You already have an active plan. To upgrade, select a higher-tier plan."
                )

        if _plan_is_expired(current_user):
            result_expired = await db.execute(
                select(models.Plan).filter(models.Plan.id == current_user.current_plan_id)
            )
            expired_plan = result_expired.scalar_one_or_none()
            if expired_plan and plan.price <= expired_plan.price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Your previous plan has expired. You must upgrade to a higher tier."
                )

        amount_usd = plan.price
        plan_name = plan.name
        payment_type = "upgrade" if current_user.current_plan_id else "purchase"

    # Case B: Pure recharge (amount-based)
    elif request_data.amount:
        if request_data.amount < 20:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Minimum recharge amount is $20."
            )
        amount_usd = request_data.amount
        payment_type = "recharge"
        plan_name = f"Recharge ${amount_usd:.2f}"
    
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either plan_id or amount must be provided."
        )

    # 4. Convert USD to KES
    usd_to_kes = float(getattr(settings, "PESAFLUX_USD_TO_KES_RATE", 130))
    amount_kes = max(1, round(amount_usd * usd_to_kes))

    # 5. Generate unique reference
    plan_ref_id = plan.id if plan else "RCH"
    reference = f"ATLAS-{current_user.id}-{plan_ref_id}-{uuid.uuid4().hex[:10].upper()}"

    # 6. Create pending PesaFluxPayment record
    pf_payment = PesaFluxPayment(
        user_id=current_user.id,
        plan_id=plan.id if plan else None,
        reference=reference,
        phone=_normalize_phone(request_data.phone),
        amount=amount_kes,
        amount_usd=amount_usd,
        status="pending",
        payment_type=payment_type,
        created_at=_utc_now()
    )
    db.add(pf_payment)
    await db.commit()

    # 7. Call PesaFlux Service to initiate STK Push
    init_res = await pesaflux_service.initiate_stk_push(
        phone=pf_payment.phone,
        amount=pf_payment.amount,
        reference=pf_payment.reference,
        description=f"Atlas {plan_name} - {current_user.email}"
    )

    if not init_res["success"]:
        # Update record as failed immediately
        pf_payment.status = "failed"
        await db.commit()
        
        # pesaflux_service.initiate_stk_push now returns correct status codes
        # and user-friendly error messages in the "message" field.
        raise HTTPException(
            status_code=init_res.get("status_code", status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=init_res.get("message", "Failed to initiate M-Pesa payment. Please try again later.")
        )

    # 8. Update record with transaction request ID
    pf_payment.transaction_request_id = init_res.get("transaction_request_id")
    await db.commit()

    return {
        "reference": reference,
        "transaction_request_id": pf_payment.transaction_request_id,
        "amount_kes": amount_kes,
        "amount_usd": amount_usd,
        "plan_name": plan_name,
        "message": "STK Push sent to your phone. Please enter your PIN to complete payment."
    }
