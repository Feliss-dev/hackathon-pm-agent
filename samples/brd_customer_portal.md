# Business Requirement Document - Customer Portal (Phase 1)

## 1. Background
The bank wants a self-service portal so retail customers can register online, replacing the paper form
processed by branch staff. Legacy customer records live in an Oracle 11g database and must move to the
new PostgreSQL platform before go-live.

## 2. Scope
### 2.1 Customer registration
- Customers register with name, email, phone and national ID.
- The system validates the national ID format and rejects duplicates.
- A confirmation email is sent after successful registration.

### 2.2 Customer profile
- Customers can view and update their contact details.
- Every change is recorded in an audit trail visible to back-office staff.

### 2.3 Data migration
- Migrate ~1.2M customer records from Oracle to PostgreSQL. Estimated 5d by the data team (Giang).
- Records missing an email must be backfilled from the CRM export.

## 3. Non-functional
- Registration API p95 latency < 300 ms.
- Unit test coverage >= 80% for backend services.

## 4. Quality
- QA (Em) prepares test cases from this document and runs two SIT cycles before UAT.

## 5. Governance
- Weekly 1-hour status meeting with the client sponsor.
- Deployment guide must be delivered to the operations team.
