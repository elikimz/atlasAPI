# Atlas API Design

This document describes the supported application API. All protected endpoints require an `Authorization: Bearer <access_token>` header. The API does not use OTP verification or email-based login challenges.

## 1. Authentication API

**Purpose:** Handles password registration, login, short-lived access tokens, and revocable refresh sessions.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/auth/register/final` | `POST` | Creates a password-based account. | `{"username":"jane_doe","password":"long-password","phone_number":"2547...","email":"jane@example.com"}` | `{"message":"Registration successful"}` | Username and email are unique; password must be 8–72 UTF-8 bytes. |
| `/auth/login` | `POST` | Authenticates by username or email and password. | `{"username":"jane_doe","password":"long-password"}` | `{"access_token":"...","refresh_token":"...","token_type":"bearer","access_token_expires_in":3600}` | Returns `401` for invalid credentials and `403` for suspended accounts. |
| `/auth/refresh` | `POST` | Rotates a valid, unrevoked refresh session. | `{"refresh_token":"..."}` | Same token pair as login | The prior refresh token is revoked atomically. |
| `/auth/logout` | `POST` | Revokes a refresh session. | `{"refresh_token":"..."}` | `{"message":"Logged out successfully"}` | Idempotent for expired or invalid local sessions. |
| `/auth/me` | `GET` | Retrieves the authenticated user's profile and role fields. | None | `{"id":1,"username":"jane_doe","role":"user",...}` | Requires bearer authentication. |

## 2. Dashboard API

**Purpose:** Provides an overview of user progress and key metrics.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/dashboard/summary` | `GET` | Retrieves summary statistics for the dashboard. | None | `{"footage_labeled_min": 0, "approved_roles": "None yet", "certifications_earned": 0}` | Requires authentication |

## 3. Training API

**Purpose:** Manages training modules, certifications, and learning resources.

### 3.1 Certifications

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/training/certifications` | `GET` | Lists available certifications. | None | `[{"id": 1, "name": "Standard Label Training", "status": "available"}]` | Requires authentication |
| `/training/certifications/{id}/start` | `POST` | Starts a specific certification. | None | `{"message": "Certification started"}` | Requires authentication |

### 3.2 Learning Hub

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/training/learning-hub` | `GET` | Retrieves learning hub content (guidelines, references, videos). | None | `{"guidelines": "...", "references": "...", "training_videos": "..."}` | Requires authentication |

## 4. Tasks API

**Purpose:** Manages user tasks and their status.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/tasks` | `GET` | Lists available tasks. | None | `[{"id": 1, "name": "Labeling Task 1", "status": "locked"}]` | Requires authentication |

## 5. Referrals API

**Purpose:** Handles referral program details and user referrals.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/referrals/summary` | `GET` | Retrieves referral program summary (earnings, users referred). | None | `{"earnings": 0.00, "users_referred": 0, "passed_training": 0}` | Requires authentication |
| `/referrals/codes` | `GET` | Lists user's referral codes. | None | `[{"code": "135I128E", "signups": 0, "trained": 0, "earned": 0.00}]` | Requires authentication |
| `/referrals/codes` | `POST` | Adds a new referral code. | `{"code": "NEWCODE"}` | `{"message": "Referral code added"}` | Requires authentication |

## 6. Payments API

**Purpose:** Manages payment information and payout history.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/payments/overview` | `GET` | Retrieves payment overview (total paid, unpaid, pending). | None | `{"total_paid": 0.00, "previous_unpaid": 0.00, "current_pending": 0.00}` | Requires authentication |
| `/payments/history` | `GET` | Lists payment history by pay period. | None | `[{"period": "May 1-15, 2026", "amount": 0.00, "status": "in progress"}]` | Requires authentication |
| `/payments/method` | `POST` | Sets up or updates payment method. | `{"type": "crypto", "details": {"wallet_address": "..."}}` | `{"message": "Payment method updated"}` | Requires authentication |

## 7. Feedback API

**Purpose:** Provides user feedback and evaluation details.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/feedback/evaluations` | `GET` | Lists user's evaluations. | None | `[{"id": 1, "name": "Tier 1 Evaluation 1", "episodes_completed": "0/5", "episodes_passing_audit": "0/0"}]` | Requires authentication |

## 8. Settings API

**Purpose:** Manages user profile and account settings.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/settings/profile` | `GET` | Retrieves user profile details. | None | `{"first_name": "John", "last_name": "Doe", "email": "user@example.com"}` | Requires authentication |
| `/settings/profile` | `PUT` | Updates user profile details. | `{"first_name": "Jane", "last_name": "Smith"}` | `{"message": "Profile updated"}` | Requires authentication |
| `/settings/discord/connect` | `POST` | Connects user's Discord account. | None | `{"message": "Discord connected"}` | Requires authentication |
| `/settings/account/delete` | `DELETE` | Deletes user account. | None | `{"message": "Account deleted"}` | Requires authentication |
| `/settings/session/signout` | `POST` | Signs out current session. | None | `{"message": "Signed out"}` | Requires authentication |
| `/settings/session/signout-all` | `POST` | Signs out all sessions. | None | `{"message": "All sessions signed out"}` | Requires authentication |
