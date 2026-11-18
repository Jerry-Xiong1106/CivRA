import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoCases } from '../src/domain/demo.js';
import { evidenceReview } from '../src/domain/evidence-review.js';
import P from '../src/domain/policy.js';

test('演示提醒仅用于与当前案情及材料版本匹配的虚构案件', () => {
  const item = createDemoCases()[0];
  assert.equal(evidenceReview(item, P.basis(item)).demo, true);
  item.files.pop();
  assert.equal(evidenceReview(item, P.basis(item)).demo, false);
  const changed = createDemoCases()[0];
  changed.fields.goal.value = '更新后的请求';
  assert.equal(evidenceReview(changed, P.basis(changed)).demo, false);
});
test('普通文件即使名称含重复或缺失也不能伪装成已完成核验', () => {
  const item = createDemoCases()[0];
  item.synthetic = false;
  const review = evidenceReview(item, P.basis(item));
  assert.equal(review.demo, false);
  assert.match(review.duplicate, /当前尚未核验/);
  assert.doesNotMatch(review.missing, /演示待核验项/);
  delete item.demoAnalysis;
  assert.equal(evidenceReview(item, P.basis(item)).demo, false);
});
