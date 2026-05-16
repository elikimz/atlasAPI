# API Design for Generic Task Management Platform

This document outlines the API design for a generic task management platform, inspired by the visual layout and user flow of the Adpulse Capture application. The goal is to create a functional backend that supports the observed UI elements and interactions, while maintaining a generic and distinct implementation.

## 1. Authentication API

**Purpose:** Handles user registration, login, and session management.

| Endpoint | Method | Description | Request Body | Response Body | Notes |
|---|---|---|---|---|---|
| `/auth/login` | `POST` | Initiates login process by sending an OTP to the provided email. | `{"email": "user@example.com"}` | `{"message": "OTP sent to email"}` | |
| `/auth/verify` | `POST` | Verifies the OTP and logs in the user, returning an access token. | `{"email": "user@example.com", "otp": "123456"}` | `{"access_token": "<jwt_token>", "token_type": "bearer"}` | |
| `/auth/me` | `GET` | Retrieves current user's profile information. | None | `{"id": 1, "email": "user@example.com", "first_name": "John", "last_name": "Doe"}` | Requires authentication |

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
