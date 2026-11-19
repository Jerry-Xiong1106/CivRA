import test from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdown } from '../src/services/markdown.js';

test('renders headings, bold, lists and quotes', () => {
  const html = renderMarkdown('## 第一步\n**诉求**：8000元\n- 认定书\n- 发票\n1. 催告\n2. 起诉\n> 民法典第一千二百一十三条');
  assert.match(html, /<h4>第一步<\/h4>/);
  assert.match(html, /<strong>诉求<\/strong>：8000元/);
  assert.match(html, /<ul><li>认定书<\/li><li>发票<\/li><\/ul>/);
  assert.match(html, /<ol><li>催告<\/li><li>起诉<\/li><\/ol>/);
  assert.match(html, /<blockquote>民法典第一千二百一十三条<\/blockquote>/);
});

test('model output cannot inject markup', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)> **<script>x</script>**');
  assert.ok(!html.includes('<img') && !html.includes('<script'));
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('renders pipe tables and escapes cells', () => {
  const html = renderMarkdown('| 步骤 | 成果 |\n|---|---|\n| 管辖 | **海淀法院** |\n| <b>x</b> | 发票 |');
  assert.match(html, /<table><thead><tr><th>步骤<\/th><th>成果<\/th><\/tr><\/thead>/);
  assert.match(html, /<td><strong>海淀法院<\/strong><\/td>/);
  assert.ok(!html.includes('<b>'));
});
