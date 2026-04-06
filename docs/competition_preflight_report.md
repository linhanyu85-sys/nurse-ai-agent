# Competition Preflight Report

Generated at: 2026-03-31 23:19:53 +08:00

Target stack: api=8000, device=8013, patient_context=8002
Target owner: linmeili (u_linmeili)

## Check Results

| Check | Result | Detail |
|---|---|---|
| license_scan | PASS | skipped_by_param |
| api_gateway_health | PASS | status=ok |
| device_gateway_health | PASS | status=ok |
| device_owner_binding | PASS | owner_user_id=u_linmeili, owner_username=linmeili |
| bed_mapping_coverage | FAIL | [verify] department=dep-card-01 coverage_mode=range bed_range=1-40 / [verify] mapped_beds_in_db=17 / [verify] missing_in_db=23 / [verify] missing_in_db_list=1,2,3,4,5,6,7,8,9,10,11,13,14,27,32,33,34,35,36,37,38,39,40 / [verify] missing_in_api=7 / [verify] missing_in_api_list=24,25,26,28,29,30,31 |
| sample_bed_selected | PASS | bed_no=12 |
| workflow_patient_query | PASS | status=completed; bed=12; patient=pat-001; stt_len=18; summary_len=272; review_required=True |
| workflow_document | PASS | status=completed; bed=12; patient=pat-001; stt_len=28; summary_len=109; review_required=True |
| workflow_handover | PASS | status=completed; bed=12; patient=pat-001; stt_len=29; summary_len=209; review_required=True |
| workflow_recommendation | PASS | status=completed; bed=12; patient=pat-001; stt_len=68; summary_len=149; review_required=True |
| db_persist_document_drafts | FAIL | before=34, after=34, delta=0 |
| db_persist_handover_records | FAIL | before=16, after=16, delta=0 |
| db_persist_ai_recommendations | FAIL | before=41, after=41, delta=0 |
| db_persist_audit_logs | PASS | before=244, after=251, delta=7 |

## DB Counters

| Table | Before | After | Delta |
|---|---:|---:|---:|
| document_drafts | 34 | 34 | 0 |
| handover_records | 16 | 16 | 0 |
| ai_recommendations | 41 | 41 | 0 |
| audit_logs | 244 | 251 | 7 |
