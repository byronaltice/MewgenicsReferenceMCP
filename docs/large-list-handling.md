# Large List Handling

How to decide where structured and tabular content lives, and how agents access it.

## The Problem

Some wiki pages are giant flat lists or tables — hundreds of abilities, thousands of body part images, every possible cat name. Embedding these as markdown files would either create thousands of tiny files or one enormous file that floods an agent's context. Neither is useful.

## Decision Tree

### Step 1: Is it structured/tabular?

Does the content have consistent columns or attributes per entry (name, type, description, element, image path, etc.)?

- **Yes** → go to Step 2
- **No** (pure prose list) → split into atomic markdown files per normal [splitting strategy](splitting-strategy.md)

### Step 2: How many rows?

- **More than ~50 rows** → SQLite table. Never embed as markdown.
- **50 rows or fewer** → markdown table inline in the relevant doc is fine. No SQLite needed.

### Step 3: Is it a hybrid page?

Does the page contain both prose (mechanic explanation, lore, context) and a large table?

- **Yes** → split them. Prose becomes one or more atomic markdown files per the splitting strategy. Table goes to SQLite. The markdown file gets a note pointing to the SQLite table (see "Linking Markdown to SQLite" below).
- **No** → handle each part according to Steps 1–2.

## What Goes in SQLite

| Content | Table name (planned) | Key columns |
|---|---|---|
| Abilities | `abilities` | name, element, type, description, icon_path |
| Body parts | `body_parts` | part_id, category, image_path |
| Cat names | `cat_names` | name |

Additional tables added as wiki content is ingested. Run `list_db_tables()` via MCP for the current schema.

## Linking Markdown to SQLite

When a markdown file's topic has related SQLite data, include a short note at the bottom:

```markdown
## Data Reference

The full abilities list for this class is in the `abilities` SQLite table.
Query: `query_db("abilities", {"class": "Warrior"})`.
```

This note gets embedded with the markdown file. An agent searching for "Warrior abilities" finds the markdown file, reads the note, and knows exactly how to query for the data — without the full table ever entering context.

## How Agents Discover SQLite Content

Agents do not need to know the schema upfront. The flow is:

1. Agent runs `search("warrior abilities")` — finds the Warrior markdown file
2. Markdown file contains a data reference note pointing to SQLite
3. Agent runs `query_db("abilities", {"class": "Warrior"}, limit=20)` for filtered results
4. Agent runs `list_db_tables()` if it needs to explore what structured data exists

Agents never receive full table dumps. All SQLite access is filtered. If no filter is specified, `limit` defaults to 20.

## Images as Structured Data

Body part images (2,500+) follow the same rule: SQLite table with `category`, `image_path`, and any other relevant attributes. Agents query for `category = "head"` and get back a list of file paths, not 2,500 entries at once.
