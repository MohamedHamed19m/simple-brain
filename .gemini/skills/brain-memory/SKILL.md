---
name: brain-memory
description: >
  Directs the main orchestrator to delegate all memory, search, note-taking, and
  retrieval requests to the unified `brain_memory_agent` subagent.
---

# Brain Memory Handoff Instructions

When the user asks to search, recall, save, or update personal notes, memories, or discoveries, you MUST delegate the task directly to the `brain_memory_agent` subagent.

## When to delegate

1. **Retrieval**: The user asks questions like:
   - "Do I have notes on...?"
   - "How did I solve... before?"
   - "Check my knowledge base for..."
   - "What do my memories say about...?"
2. **Curation**: The user asks to write down or update memories:
   - "Save today's discovery about..."
   - "Remember that we need to..."
   - "Update my note on..."
   - "Add this to my skills/knowledge..."

## How to delegate

Do not call CLI commands (like `brain`) or read markdown files in the vault directly. Instead, invoke the subagent tool for `brain_memory_agent` (or type `@brain_memory_agent` if prompting manually) and pass the user's request. 

Let `brain_memory_agent` handle the SQLite FTS5 search, duplicate checking, writing, and summarization in its isolated context, and only use the summarized response it returns.
