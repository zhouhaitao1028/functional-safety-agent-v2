# Evidence Pack contract

Every material business claim should point to one or more retrieved evidence items. Preserve these fields without rewriting their identity:

```json
{
  "knowledge_id": "stable knowledge ID",
  "chunk_id": "stable chunk ID",
  "source_path": "original file or registered source",
  "locator": "section, paragraph, table row or worksheet cell range",
  "version": "source version",
  "status": "draft|approved|retired",
  "content": "retrieved excerpt",
  "score": 0.0
}
```

Agent output should refer to evidence with `knowledge_id` and `chunk_id` rather than copying large passages. A `draft` item cannot silently support a production conclusion. When sources conflict, return both references and identify the conflict for human review.

Graph context is supporting relationship evidence, not a replacement for the underlying source excerpt.
