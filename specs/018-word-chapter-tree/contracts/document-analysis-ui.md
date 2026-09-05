# Contract: `/analysis` Stateless Document Analysis UI

## URL state

```text
/analysis?tab=document
```

- `tab` accepts `reasoning | graph | document`; invalid/missing values use `reasoning`.
- `job_id` and `node_id` are not document-tool state. Legacy values are removed when the document tab is opened.
- The uploaded `File`, API response, error/loading state, and selected node ID live only in component state.
- Refreshing, closing, or clearing the page discards the file and response.

## Upload state

The empty view accepts one `.doc` or `.docx` by file picker or drag/drop and immediately calls `POST /api/document-analysis/word`. It does not list ExtractionJobs or request annotated-document.

After success, the toolbar provides:

- current filename and local file size;
- pagination and summary badges;
- re-analyze the same in-memory file;
- choose another file;
- clear the local result.

The UI explicitly states that the operation creates no extraction job, classification, entity, relationship, triple, cache, or graph write.

## Tree mapping

ChapterNode children remain section children. A leaf ChapterNode appends its PageNodes as display children only when it spans multiple page fragments; a single-page leaf remains directly selectable without a redundant page child. Selection IDs are backend node IDs without title-derived rewriting. `selectedNodeId` is component-local and defaults to the document root after each successful upload.

## WordViewer location

```ts
export interface WordViewerLocation {
  kind: "document" | "section" | "page";
  nodeId: string;
  anchorBlockId?: string | null;
  startBlockId?: string | null;
  endBlockId?: string | null;
  blockIds?: string[];
}
```

On location change the viewer:

1. Removes the previous active-location CSS class.
2. Indexes rendered elements by `data-source-block-id`, then resolves either exact page `blockIds` or the chapter start/end range.
3. Adds the class to matching elements and scrolls the first match into view.
4. Does not call `editor.commands.setContent()` because the `content` reference stays unchanged during node selection.

## Display states

- Empty upload, current-request analysis, request error, and completed analysis have separate views; there is no job-processing state.
- Summary badges map `llm`→AI 生成, `extractive_fallback`→自动摘录, `empty`→无可摘要内容, `none`→未生成.
- Single-page leaf chapters use the leaf-document icon; multi-page leaves remain expandable and expose their page fragments.
- Physical page label is `第 N 页` only when a number exists; otherwise `章节页 N`.
- Desktop: tree/original/metadata columns. Narrow screens: original stays primary and the other panels remain reachable via sheets.

## Performance contract

- Each file selection produces exactly one upload request.
- Node selection produces zero requests.
- Annotated content is passed unchanged while selecting nodes.
- Node index and display tree are memoized from `section_tree`.
