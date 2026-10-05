# Phases 1–3 requirement audit

The objective remains completion of all three phases in `ROADMAP.md`. UAVPal has now been supplied, source-verified and imported as a real training tile; this does not waive the phases' real-data, reference, model or evaluation gates. This audit separates implemented software from unproved completion requirements.

The current verification run passes 39 tests (27 new workflow tests and 12 existing regression checks). Two original asset-dependent checks are excluded. Chrome passes 17 workflow checks against a declared synthetic site. Synthetic QA establishes behavior, not model accuracy or an independently verified geospatial overlay.

| Requirement | Current evidence | Remaining requirement |
|---|---|---|
| P1.1 Restore baseline/environment | Python 3.12 environment, dependency files and doctor/serve commands; compilation and available mathematical/model regressions pass | Original RGB/bundle restoration and historical-site baseline smoke run (both public checkpoints are restored; an Indian training-tile job succeeded); reproduction on the designated demo OS |
| P1.2 Pilot and evaluation assets | UAVPal: 1,602 checksum-matched files; 529 aligned RGB/label/DSM triplets; five semantic classes | 70/28/21 spatial preparation is frozen; pilot/reference evaluation remains pending; DTM, parcel/survey references and independent checkpoints |
| P1.3 Site manifest and compatibility | Synthetic onboarding/site-isolation tests verify new manifests; registry retains legacy catalog adapter | Real La Paz bundle compatibility and second-site validation |
| P1.4 Ingestion and validation | Upload/tile/invalid-raster/CRS tests and elevation coverage/alignment checks pass; height preparation rejects missing review/evidence | Independent horizontal/vertical controls and real alignment review |
| P1.5 Per-site analysis CRS | Metric area, topology, merge/split, export and invalid-unit checks pass in EPSG:32643 fixtures | Overlay/measurements on the restored historical site and real new site |
| P1.6 Imagery delivery | COG layout, quicklook, tile caching and browser tile checks pass | Real source display/inference-resolution validation and workload measurements |
| P2.1 Backend/UI integration | API site selection, paged viewport loading and browser workflow operate without a static bundle | Legacy real bundle comparison and representative workload |
| P2.2 Persistent review operations | API tests cover edit/draw/merge/split/reset/remove and server restart; browser verifies editing and reload | Real review tasks and historical bundle compatibility |
| P2.3 Revision/history | Stale and concurrent writes, before/after audit and edit invalidation verified | Representative pilot review audit; reviewer labels remain local assertions |
| P2.4 Canonical exports | Saved/historical snapshots, projected GeoPackage and WGS84 exports verified; use references survive export | Open real pilot products in external GIS and verify overlays; historical asset-based suite |
| P2.5 Review efficiency | Queue, operation history and reported UI interaction intervals available and tested | Controlled real-task review effort and quality comparison; intervals are not measured active labour |
| P3.1 Extraction jobs | Real local subprocess import checks plus failure/retry, restart reconciliation, queued/running cancellation and browser publication pass | An imported Bhopal training-tile Mask R-CNN job succeeded; representative reference scoring remains pending |
| P3.2 Local reference protocol | Reviewed/hash/grid/spatial-split validation and leakage checks pass | Reviewed real labels, adequate class/instance coverage and fresh evaluation blocks |
| P3.3 Building improvements | Local-checkpoint Mask R-CNN fine-tuning and frozen experiment/evaluation tools implemented | Restore starting weights, train and evaluate; conditional boundary-aware WHU experiment follows measured residuals |
| P3.4 Roads/access | Cover U-Net supports road surfaces; skeleton centerlines, corridor scores and connectivity diagnostics tested | Reviewed narrow-road/pathway references, occlusion assessment and independent route/connectivity evaluation |
| P3.5 Cover and functional use | Temporary CPU cover and use train/evaluate/register workflows pass. Use-classification worker freezes source features and publishes suggestions without promoting labels; browser verifies its job form. Inspector corrections require references and retain geometry-bound history; edit/split invalidation tested | Actual use labels, locally agreed classes, model suitability and fresh class/unknown evaluation. Reviewer assertions are not independent source authentication |
| P3.6 Height ablations | Reviewed aligned nDSM preparation, source hashes, training height inputs and inference guards implemented/tested | Verified real DSM/DTM, vertical reference and paired RGB/height evaluations |

All three completion gates remain unproved. The identified Phase 1–3 software foundations now have synthetic workflow evidence; the remaining original-asset, additional-reference, training and evaluation gates remain pending as UAVPal preparation begins. Existing approved artifacts and tests remain unchanged. Synthetic performance, assertions and labels cannot satisfy those gates.

The user subsequently requested minimum Phase 4–5 coding under a time constraint. That delivered scope is documented in [MINIMUM_DEMO.md](MINIMUM_DEMO.md), independently of the complete roadmap gates.
