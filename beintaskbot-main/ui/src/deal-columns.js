// Metadata comes from the authenticated, pipeline-scoped server catalog.
import {stageStyle} from './deal-stage-style.js';
export const stageKey = (pipeline, stage) => `${pipeline}:${stage}`;
export function dealColumns(rows, catalog = [], hidden = []) {
  const result = new Map();
  for (const p of catalog) for (const [index, s] of (p.stages || []).entries()) {
    const key = stageKey(p.pipeline_id, s.stage_id);
    result.set(key, {key, pipeline: p.name || `#${p.pipeline_id}`, name: s.name || `#${s.stage_id}`, appearance: stageStyle(s.color, index), rows: []});
  }
  for (const row of rows) {
    const key = stageKey(row.pipeline_id, row.status_id);
    // Own-created deals may be outside the assigned pipeline catalog. Do not
    // infer that pipeline's other stages from a single authorized deal.
    if (!result.has(key)) result.set(key, {key, pipeline: row.pipeline_name || `#${row.pipeline_id}`, name: row.stage_name || `#${row.status_id}`, appearance: stageStyle(row.stage_color, Number(row.status_id) || 0), rows: []});
    result.get(key).rows.push(row);
  }
  const excluded = new Set(hidden);
  return [...result.values()].filter(column => !excluded.has(column.key));
}
