# Building detection scores (32 approved labels, 4 windows)

Eval grid 0.1 m; building match IoU >= 0.5; pieces < 2.0 m^2 dropped.

| run | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| whu_baseline | 0.970 | 0.845 | 0.824 | 0.583 | 0.219 | 7 | 12 | 32 | 0.749 |
| rgb_guided_v1_unreviewed | 0.970 | 0.845 | 0.824 | 0.667 | 0.312 | 10 | 15 | 32 | 0.747 |
| rgb_guided_reviewed | 0.970 | 0.845 | 0.824 | 0.643 | 0.281 | 9 | 14 | 32 | 0.773 |

## Secondary reference (Microsoft ML footprints - NOT ground truth)

| source | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| microsoft | 0.659 | 0.608 | 0.463 | 0.000 | 0.000 | 0 | 24 | 32 | n/a |

## Per window

- whu_baseline / W1_dense_informal: pixel_precision=0.936, pixel_recall=0.781, pixel_iou=0.741, building_precision=0.333, building_recall=0.125, matched=2, n_pred=6, n_label=16, mean_matched_iou=0.608
- whu_baseline / W2_formal_residential: pixel_precision=0.965, pixel_recall=0.898, pixel_iou=0.870, building_precision=1.000, building_recall=0.667, matched=2, n_pred=2, n_label=3, mean_matched_iou=0.865
- whu_baseline / W3_large_sheds: pixel_precision=0.991, pixel_recall=0.843, pixel_iou=0.837, building_precision=0.500, building_recall=0.091, matched=1, n_pred=2, n_label=11, mean_matched_iou=0.588
- whu_baseline / W4_vegetation: pixel_precision=0.969, pixel_recall=0.932, pixel_iou=0.904, building_precision=1.000, building_recall=1.000, matched=2, n_pred=2, n_label=2, mean_matched_iou=0.854
- rgb_guided_v1_unreviewed / W1_dense_informal: pixel_precision=0.936, pixel_recall=0.781, pixel_iou=0.741, building_precision=0.429, building_recall=0.188, matched=3, n_pred=7, n_label=16, mean_matched_iou=0.711
- rgb_guided_v1_unreviewed / W2_formal_residential: pixel_precision=0.965, pixel_recall=0.898, pixel_iou=0.870, building_precision=1.000, building_recall=0.667, matched=2, n_pred=2, n_label=3, mean_matched_iou=0.865
- rgb_guided_v1_unreviewed / W3_large_sheds: pixel_precision=0.991, pixel_recall=0.843, pixel_iou=0.837, building_precision=0.750, building_recall=0.273, matched=3, n_pred=4, n_label=11, mean_matched_iou=0.632
- rgb_guided_v1_unreviewed / W4_vegetation: pixel_precision=0.969, pixel_recall=0.932, pixel_iou=0.904, building_precision=1.000, building_recall=1.000, matched=2, n_pred=2, n_label=2, mean_matched_iou=0.854
- rgb_guided_reviewed / W1_dense_informal: pixel_precision=0.936, pixel_recall=0.781, pixel_iou=0.741, building_precision=0.429, building_recall=0.188, matched=3, n_pred=7, n_label=16, mean_matched_iou=0.711
- rgb_guided_reviewed / W2_formal_residential: pixel_precision=0.965, pixel_recall=0.898, pixel_iou=0.870, building_precision=1.000, building_recall=0.667, matched=2, n_pred=2, n_label=3, mean_matched_iou=0.865
- rgb_guided_reviewed / W3_large_sheds: pixel_precision=0.991, pixel_recall=0.843, pixel_iou=0.837, building_precision=0.667, building_recall=0.182, matched=2, n_pred=3, n_label=11, mean_matched_iou=0.694
- rgb_guided_reviewed / W4_vegetation: pixel_precision=0.969, pixel_recall=0.932, pixel_iou=0.904, building_precision=1.000, building_recall=1.000, matched=2, n_pred=2, n_label=2, mean_matched_iou=0.854
- microsoft_ml_footprints / W1_dense_informal: pixel_precision=0.453, pixel_recall=0.495, pixel_iou=0.310, building_precision=0.000, building_recall=0.000, matched=0, n_pred=12, n_label=16, mean_matched_iou=n/a
- microsoft_ml_footprints / W2_formal_residential: pixel_precision=0.502, pixel_recall=0.434, pixel_iou=0.303, building_precision=0.000, building_recall=0.000, matched=0, n_pred=4, n_label=3, mean_matched_iou=n/a
- microsoft_ml_footprints / W3_large_sheds: pixel_precision=0.904, pixel_recall=0.784, pixel_iou=0.724, building_precision=0.000, building_recall=0.000, matched=0, n_pred=6, n_label=11, mean_matched_iou=n/a
- microsoft_ml_footprints / W4_vegetation: pixel_precision=0.398, pixel_recall=0.334, pixel_iou=0.222, building_precision=0.000, building_recall=0.000, matched=0, n_pred=2, n_label=2, mean_matched_iou=n/a
