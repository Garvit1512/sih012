# Building detection scores (49 approved labels, 4 windows)

Eval grid 0.1 m; building match IoU >= 0.5; pieces < 2.0 m^2 dropped.

| run | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| whu_baseline | 0.966 | 0.817 | 0.794 | 0.600 | 0.184 | 9 | 15 | 49 | 0.722 |
| rgb_guided_reviewed_final | 0.966 | 0.817 | 0.794 | 0.684 | 0.265 | 13 | 19 | 49 | 0.730 |

## Secondary reference (Microsoft ML footprints - NOT ground truth)

| source | pixel_precision | pixel_recall | pixel_iou | building_precision | building_recall | matched | n_pred | n_label | mean_matched_iou |
|---|---|---|---|---|---|---|---|---|---|
| microsoft | 0.713 | 0.633 | 0.505 | 0.100 | 0.041 | 2 | 20 | 49 | 0.607 |

## Per window

- whu_baseline / V1_dense_informal: pixel_precision=0.950, pixel_recall=0.859, pixel_iou=0.821, building_precision=0.000, building_recall=0.000, matched=0, n_pred=3, n_label=13, mean_matched_iou=n/a
- whu_baseline / V2_large_sheds: pixel_precision=0.981, pixel_recall=0.981, pixel_iou=0.962, building_precision=0.000, building_recall=0.000, matched=0, n_pred=1, n_label=10, mean_matched_iou=n/a
- whu_baseline / V3_residential: pixel_precision=0.945, pixel_recall=0.599, pixel_iou=0.579, building_precision=1.000, building_recall=0.438, matched=7, n_pred=7, n_label=16, mean_matched_iou=0.742
- whu_baseline / V4_small_roofs: pixel_precision=0.963, pixel_recall=0.640, pixel_iou=0.625, building_precision=0.500, building_recall=0.200, matched=2, n_pred=4, n_label=10, mean_matched_iou=0.650
- rgb_guided_reviewed_final / V1_dense_informal: pixel_precision=0.950, pixel_recall=0.859, pixel_iou=0.821, building_precision=0.000, building_recall=0.000, matched=0, n_pred=3, n_label=13, mean_matched_iou=n/a
- rgb_guided_reviewed_final / V2_large_sheds: pixel_precision=0.981, pixel_recall=0.981, pixel_iou=0.962, building_precision=0.000, building_recall=0.000, matched=0, n_pred=2, n_label=10, mean_matched_iou=n/a
- rgb_guided_reviewed_final / V3_residential: pixel_precision=0.945, pixel_recall=0.599, pixel_iou=0.579, building_precision=0.875, building_recall=0.438, matched=7, n_pred=8, n_label=16, mean_matched_iou=0.760
- rgb_guided_reviewed_final / V4_small_roofs: pixel_precision=0.963, pixel_recall=0.640, pixel_iou=0.624, building_precision=1.000, building_recall=0.600, matched=6, n_pred=6, n_label=10, mean_matched_iou=0.694
- microsoft_ml_footprints / V1_dense_informal: pixel_precision=0.698, pixel_recall=0.746, pixel_iou=0.564, building_precision=0.400, building_recall=0.154, matched=2, n_pred=5, n_label=13, mean_matched_iou=0.607
- microsoft_ml_footprints / V2_large_sheds: pixel_precision=0.971, pixel_recall=0.664, pixel_iou=0.651, building_precision=0.000, building_recall=0.000, matched=0, n_pred=3, n_label=10, mean_matched_iou=n/a
- microsoft_ml_footprints / V3_residential: pixel_precision=0.594, pixel_recall=0.703, pixel_iou=0.475, building_precision=0.000, building_recall=0.000, matched=0, n_pred=8, n_label=16, mean_matched_iou=n/a
- microsoft_ml_footprints / V4_small_roofs: pixel_precision=0.256, pixel_recall=0.174, pixel_iou=0.115, building_precision=0.000, building_recall=0.000, matched=0, n_pred=4, n_label=10, mean_matched_iou=n/a
