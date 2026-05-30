# Sample PDFs for GDPR Demo

Drop your 15 demo PDFs here. Expected types:

| File | Expected Type |
|------|--------------|
| Supplier onboarding forms | supplier_onboarding |
| Expense reports | expense_report |
| IT access request forms | it_access_request |
| Incident reports | incident_report |
| Training evaluations | training_evaluation |

## Parsing Expectations

- **Supplier** files: Company, Address, Contact, Tax ID
- **Expense** files: Employee, Amount, Date, Manager
- **IT Access** files: Name, System, Access Level, Signature

## Owner Hints

Edit `../owner_hints.json` to map files to owners.
Files without owner hints will be escalated to DPO.
