# Content Splitting Strategy

How wiki pages are converted into atomic markdown files for embedding and retrieval.

## Goal

Each file should be independently useful — an agent reading only that file gets a complete, coherent piece of information without needing to load anything else.

## What Counts as Atomic

A file is atomic if it covers exactly one concept: one class, one mechanic, one ability, one location, one status effect. A concept is "one thing" if:

- It has its own identity (a name, a distinct set of properties)
- An agent could reasonably ask about it in isolation ("what does the Warrior class do?")
- It does not require sibling content to make sense

When in doubt, split smaller. Two small files are better than one file that covers two things.

## Splitting Rules

### Multi-entity pages

A wiki page that covers many entities of the same type (e.g. "Classes" listing all classes) becomes:

```
classes/
  index.md          ← lists all classes, links to each, one-line description per class
  warrior.md        ← full detail on Warrior
  mage.md           ← full detail on Mage
  ...
```

The index file exists so an agent can discover what entities exist without loading all of them. The individual files exist so an agent can load only what it needs.

### Single-entity pages

A wiki page covering one thing in detail stays as one file. No splitting needed unless distinct sub-concepts within it are independently queryable.

### Mechanic pages

Pages describing a system or mechanic (e.g. "Breeding", "Combat", "Houses") are split by sub-mechanic if the page covers multiple distinct systems, or kept whole if it describes one cohesive system.

If a mechanic page contains a table of data, see [large-list-handling.md](large-list-handling.md) — the prose becomes a markdown file and the table goes to SQLite.

### Cross-references

When one atomic file refers to another concept, use a plain text reference ("see Warrior class") rather than a relative file path. Paths are implementation details and will break if files move.

## File Naming

- Lowercase, hyphen-separated: `holy-beam.md`, `warrior-class.md`
- Grouped by type in subdirectories: `abilities/`, `classes/`, `mechanics/`, `items/`
- Index files always named `index.md` within their directory

## What to Preserve

- All prose from the original page
- Images: download alongside the markdown file, reference with a relative path
- Tables: evaluate against [large-list-handling.md](large-list-handling.md) before keeping inline

## What to Drop

- Wiki navigation chrome (sidebars, headers, footers, "edit this page" links)
- Patch history and changelogs
- Stub notices and maintenance tags
- Duplicate content that appears verbatim in another file
