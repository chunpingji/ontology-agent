import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const extractionPage = readFileSync(
  new URL("../src/app/(dashboard)/entities/extraction/page.tsx", import.meta.url),
  "utf8",
);
const jobForm = readFileSync(
  new URL("../src/components/extraction/job-create-form.tsx", import.meta.url),
  "utf8",
);
const reportDetail = readFileSync(
  new URL("../src/app/(dashboard)/reports/[reportId]/page.tsx", import.meta.url),
  "utf8",
);
const analysisPanel = readFileSync(
  new URL("../src/components/analysis/document-analysis-panel.tsx", import.meta.url),
  "utf8",
);

test("ordinary Word recognition only starts through document-analysis runs", () => {
  assert.doesNotMatch(apiSource, /analyzeWordDocument|document-analysis\/word/);
  assert.match(apiSource, /fetchAPI<DocumentAnalysisCreateResponse>\("\/api\/document-analysis\/runs"/);
  assert.match(analysisPanel, /createDocumentAnalysisRun\(/);

  assert.doesNotMatch(extractionPage, /source_type:\s*["']word["']/);
  assert.doesNotMatch(extractionPage, /rerunAnnotation/);
  assert.doesNotMatch(jobForm, /SelectItem value=["']word["']/);
  assert.doesNotMatch(reportDetail, /rerunAnnotation|subscribeJobProgress/);
  assert.match(reportDetail, /href="\/analysis\?tab=document"/);
});

test("doc_repo Word upload is explicitly source-preview-only", () => {
  assert.match(apiSource, /purpose:\s*p\.sourceType === "word" \? "doc_repo_preview"/);
  assert.match(apiSource, /purpose\?: "doc_repo_preview"/);
  assert.match(apiSource, /fd\.append\("purpose", params\.purpose\)/);
});

test("structured and template workflows retain their shared clients", () => {
  assert.match(extractionPage, /source_type: "excel"/);
  assert.match(jobForm, /SelectItem value="excel"/);
  assert.match(apiSource, /\/api\/extraction\/jobs\/\$\{jobId\}\/annotation\/\$\{action\}/);
});

