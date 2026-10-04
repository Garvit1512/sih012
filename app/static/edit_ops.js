/* Pure GeoJSON editing operations for the review workspace (browser + Node).
 * AI predictions are never mutated: workspace features are copies with their own provenance.
 * origin values: ai_copy (unchanged AI geometry), human_edited, human_merged, human_split, human_drawn.
 * review_status values: suggested, accepted, rejected.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.EditOps = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const DISCLAIMER =
    "AI-assisted building footprints. NOT legal cadastral parcel boundaries; not verified against authoritative parcel data.";
  let counter = 0;
  function newId() {
    counter += 1;
    return "ws-" + Date.now().toString(36) + "-" + counter.toString(36);
  }
  function clone(o) {
    return JSON.parse(JSON.stringify(o));
  }
  function isPoly(g) {
    return g && (g.type === "Polygon" || g.type === "MultiPolygon");
  }

  function fromCandidate(feature) {
    const p = feature.properties || {};
    return {
      type: "Feature",
      id: newId(),
      geometry: clone(feature.geometry),
      properties: {
        origin: "ai_copy",
        review_status: "suggested",
        source_layer: p.layer,
        source_id: p.source_id,
        ai_score: p.score,
        ai_flags: p.flags || "",
        parents: [],
      },
    };
  }

  function setStatus(feature, status) {
    if (!["suggested", "accepted", "rejected"].includes(status)) throw new Error("bad status " + status);
    const f = clone(feature);
    f.properties.review_status = status;
    return f;
  }

  function withEditedGeometry(feature, geometry) {
    if (!isPoly(geometry)) throw new Error("edited geometry must be a polygon");
    const f = clone(feature);
    f.geometry = clone(geometry);
    if (f.properties.origin === "ai_copy") f.properties.origin = "human_edited";
    return f;
  }

  function drawn(geometry) {
    if (!isPoly(geometry)) throw new Error("drawn geometry must be a polygon");
    return {
      type: "Feature",
      id: newId(),
      geometry: clone(geometry),
      properties: { origin: "human_drawn", review_status: "accepted", parents: [] },
    };
  }

  function provenance(features) {
    const layers = new Set(), ids = [];
    features.forEach((f) => {
      if (f.properties.source_layer) layers.add(f.properties.source_layer);
      ids.push(f.id);
    });
    return { layers: Array.from(layers).join(","), ids };
  }

  function merge(features, turf) {
    if (features.length < 2) throw new Error("select at least two polygons to merge");
    let g = features[0];
    for (let i = 1; i < features.length; i++) {
      const u = turf.union(turf.featureCollection([g, features[i]]));
      if (!u) throw new Error("union failed");
      g = u;
    }
    const pv = provenance(features);
    return {
      type: "Feature",
      id: newId(),
      geometry: g.geometry,
      properties: { origin: "human_merged", review_status: "accepted", source_layer: pv.layers, parents: pv.ids },
    };
  }

  /* Split a polygon with a drawn polyline. The line is buffered by `gapM` metres (default 2 cm) and
   * subtracted; each resulting part becomes a new feature. Area lost to the gap is reported. */
  function split(feature, line, turf, gapM) {
    gapM = gapM || 0.02;
    if (!line || line.geometry.type !== "LineString") throw new Error("split needs a LineString");
    const cut = turf.buffer(line, gapM / 2, { units: "meters" });
    const diff = turf.difference(turf.featureCollection([feature, cut]));
    if (!diff) throw new Error("split removed the whole polygon");
    const parts =
      diff.geometry.type === "MultiPolygon"
        ? diff.geometry.coordinates.map((c) => ({ type: "Polygon", coordinates: c }))
        : [diff.geometry];
    if (parts.length < 2) throw new Error("line does not cross the polygon completely");
    const before = turf.area(feature);
    const out = parts.map((geom) => ({
      type: "Feature",
      id: newId(),
      geometry: geom,
      properties: {
        origin: "human_split",
        review_status: "accepted",
        source_layer: feature.properties.source_layer,
        source_id: feature.properties.source_id,
        parents: [feature.id],
      },
    }));
    const after = out.reduce((s, f) => s + turf.area(f), 0);
    return { parts: out, area_lost_m2: before - after };
  }

  function exportCollection(features, statuses) {
    statuses = statuses || ["accepted"];
    const sel = features.filter((f) => statuses.includes(f.properties.review_status));
    return {
      type: "FeatureCollection",
      name: "building_footprints_reviewed",
      disclaimer: DISCLAIMER,
      features: sel.map((f) => ({
        type: "Feature",
        geometry: f.geometry,
        properties: Object.assign({ workspace_id: f.id, feature_type: "building_footprint" }, f.properties, {
          parents: (f.properties.parents || []).join(","),
          disclaimer: DISCLAIMER,
        }),
      })),
    };
  }

  return { DISCLAIMER, newId, fromCandidate, setStatus, withEditedGeometry, drawn, merge, split, exportCollection };
});
