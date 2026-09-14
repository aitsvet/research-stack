# Publication clearance

Use this procedure before making the repository, a branch, a tag, or an
archive public. It is a content-boundary review, not merely a secret scan. A
technically harmless string can still disclose a person, a private project, or
the subject of work that does not belong in this repository.

## Scope

Review only material that can be published from Git:

- the tracked working tree;
- every commit reachable from every local branch and tag intended for export;
- commit messages, author and committer metadata, tag messages, and ref names;
- every path and blob reachable from those refs;
- submodule URLs, remotes, and repository configuration that will accompany a
  clone or an archive.

Do not open or search ignored working-tree content as part of this clearance.
Ignored state, caches, local corpora, databases, generated documents, and
credentials are outside this procedure unless the user separately authorizes
their inspection. Untracked files are not publication inputs, but confirm that
none will be added accidentally.

The review order is deliberate:

1. private and identifying information;
2. unrelated projects, infrastructure, and domains of work;
3. project- or corpus-specific research material;
4. credentials and conventional secret patterns;
5. ordinary technical quality issues, only where they affect disclosure.

Passing the last item never compensates for failing an earlier one.

## Legitimate repository topics

The following subjects are part of the public research stack and are not, by
themselves, evidence of a leak:

- Zotero operation, its local API/MCP integration, and peer library sync;
- literature discovery and acquisition through Crossref, OpenAlex, Unpaywall,
  arXiv, DOI resolvers, and other public scholarly indexes;
- browser-assisted retrieval, anti-bot diagnosis, proxies, OCR, PDF repair,
  text extraction, table extraction, citation checking, and quote checking;
- Markdown, office-document, PDF, diagram, and presentation conversion;
- generic drafting, editing, front-matter, bibliography, author, adviser,
  classification-label, submission, and reusable document-template behavior;
- Docker/Compose services, Chromium, LibreOffice, PlantUML, Jupyter,
  Open WebUI, OpenCode, and model-provider integration used by the stack;
- reusable routes to public standards, regulatory, legal, or industry
  document providers, including MegaNorm, IIFM, and government portals;
- generic placeholders such as `<project>`, `<document>`, `<code>`,
  `user@peer`, example-domain email addresses, loopback addresses, environment
  variable references, and obviously non-secret sample values.

Named services are acceptable only when the name is necessary to explain or
implement a reusable integration or retrieval route. A public site name is not
an automatic exemption for a path, query, title, identifier, or narrative tied
to one private body of work.

## Material that does not belong

Reject or neutralize content in any snapshot when it contains:

- a real person's name, username, contact detail, account identifier, home
  directory, workstation path, messaging destination, or private storage
  layout, except for an explicitly approved Git publication identity;
- a private repository, neighboring checkout, personal automation, private
  host, internal service, organization-only endpoint, or unrelated project;
- the title, author, institution, venue, classification value, subject matter,
  figure name, asset name, directory layout, or prose of a particular
  manuscript or corpus;
- concrete item keys, collection keys, document identifiers, search terms, or
  filenames copied from a private research task when a placeholder would work;
- notes, journals, drafts, source documents, extracted text, generated office
  files, images, databases, browser profiles, or other research artefacts;
- passwords, tokens, private keys, authenticated URLs, cookies, session data,
  bearer headers, or realistic credential-shaped sample values;
- comments and examples that reveal why a private customization existed even
  after its literal identifier has been renamed.

Git author and committer identity, plus the account visible in the public
remote URL, may remain only when the repository owner has explicitly approved
them as the intended publication identity. That exception does not extend to
identities embedded in examples, documents, fixtures, or generated metadata.

## Distinguishing reusable support from a leak

For every proper noun, unusual path, narrow feature, and domain-specific term,
ask these questions in order:

1. Does removing it break a reusable capability that belongs to the topic list
   above?
2. Is the exact value required by a public protocol or provider, or could a
   parameter/placeholder express the same behavior?
3. Would an independent user understand the example without access to the
   original private project?
4. Does the value select one person's document, corpus, account, institution,
   route, or research question?
5. Is the same information repeated in a filename, comment, test fixture,
   default value, Compose mount, log excerpt, or historical version?

Keep a named integration when questions 1–3 establish that it is genuinely
reusable and questions 4–5 do not tie it to private work. Otherwise remove the
example, parameterize the value, or move the material to its owning project.

Generic functionality may remain even when one former example was private.
For example, retain a parser, renderer, or lint exclusion for a standard label
while replacing a concrete classification value with `<code>`. Likewise,
retain a document-export function while ensuring its sample title, author,
venue, paths, and assets are fictional placeholders.

## Review procedure

### 1. Establish the publication boundary

Record the exact refs that will be published and verify the topology:

```bash
git status --short --branch
git show-ref --head
git rev-list --all --count
git rev-list --max-parents=0 --all
git log --all --graph --decorate --oneline
```

Check branches, tags, notes, stashes, replace refs, and submodules separately.
Do not assume `main` is the only ref merely because it is checked out.

### 2. Inventory the current tracked tree

Use Git as the file boundary so ignored content is never searched:

```bash
git ls-files
git grep -n -I -i -E '<review-pattern>' HEAD --
git ls-tree -r -l HEAD
```

Review filenames as content. Look for personal names, private directory names,
specific titles, opaque identifiers, neighboring checkouts, and binary
artefacts. Read semantically suspicious files in context; a zero-result regex
is not a semantic clearance.

Pay particular attention to:

- documentation examples and troubleshooting narratives;
- defaults in scripts and Compose volume mounts;
- tests and fixtures, which often preserve real inputs after production code
  has been generalized;
- comments, usage strings, sample commands, environment examples, and URLs;
- office templates and archives, whose visible body is only one data-bearing
  part of the package.

### 3. Inspect every reachable historical snapshot

List the complete object/path inventory and search every revision, not only the
tip or the textual patch stream:

```bash
git rev-list --objects --all
for rev in $(git rev-list --all); do
  git grep -n -I -i -E '<review-pattern>' "$rev" -- || true
done
git log --all --name-status
git log --all --format=fuller
```

Build review patterns from categories and from findings made during semantic
reading. Include spelling, case, transliteration, path, URL, and filename
variants. Search commit and tag messages independently because `git grep`
searches trees, not commit objects.

When a suspicious value appears, identify its first introduction and every
later move or modification. Clearing only the current path is insufficient;
every reachable snapshot must be safe independently.

### 4. Review structured and packaged files

Treat archives and office documents as containers. A reusable template must be
checked for all of the following, not just visible body paragraphs:

- document properties and application/custom properties;
- headers, footers, footnotes, endnotes, comments, revisions, and custom XML;
- embedded media, thumbnails, attachments, and linked-object relationships;
- creator, last-editor, company, template, title, keyword, and description
  fields;
- external relationships and filenames inside the package.

Prefer a generated minimal template or an allowlist of required package parts
over copying an accepted document and deleting a few known fields. After
sanitizing, unpack the result and scan every member before committing it.

### 5. Perform the technical secret pass

After semantic clearance, scan all reachable blobs for:

- private-key headers and cloud/provider token formats;
- credential assignments with literal values;
- authenticated URLs and authorization headers;
- high-entropy strings and long opaque identifiers;
- secrets embedded in historical versions of environment or configuration
  files.

Inspect each result rather than deleting mechanically. Environment-variable
references and unmistakable placeholders are acceptable; realistic sample
tokens are not. Seed the scanner with harmless synthetic fixtures when
possible so a clean report also proves that each rule executed.

### 6. Validate the object database and export shape

For a recreated publication repository, old objects should not exist locally:

```bash
git fsck --full --unreachable --no-reflogs
git branch --all
git tag --list
git stash list
git notes list
```

Confirm the intended root count, commit count, linear/merge topology, commit
subjects, approved identity, and dates. A history rewrite necessarily changes
the affected commit IDs; it should not silently collapse useful development
history or alter unrelated metadata.

### 7. Verify from a fresh publication clone

After pushing to the newly created public remote:

1. clone it into a new temporary directory;
2. enumerate the server-advertised refs;
3. repeat the tracked-tree, history, metadata, packaged-file, and secret scans;
4. confirm that no old commit ID resolves;
5. compare the expected commit count and topology;
6. confirm that the working tree is clean and the public branch matches its
   remote tracking branch.

The fresh-clone pass is the release gate. A private working copy can contain
reflogs, alternate object stores, or local refs that do not describe what the
server actually publishes.

## Rewriting findings safely

When a finding exists in history:

1. keep a private backup outside the publication repository;
2. locate the earliest affected snapshot;
3. replace the content at its natural introduction point with a neutral,
   reusable equivalent;
4. replay later development so the feature history remains intelligible;
5. preserve commit order, purpose, authorship, dates, and topology unless a
   specific field itself is disallowed;
6. ensure no rewritten commit becomes a misleading partial implementation;
7. recreate the public remote and push only the cleared refs;
8. perform the fresh-clone gate above.

Do not commit a cleanup checklist containing the values being removed. Keep
such evidence private, because the checklist would recreate the disclosure in
the sanitized history.

## Clearance result

Clearance is complete only when all of these statements are true:

- every published ref and reachable snapshot passed semantic review;
- each proper noun and narrow feature is either within the legitimate topic
  inventory or justified as a reusable public integration;
- examples contain placeholders rather than private values;
- no corpus, manuscript, generated document, or package metadata is present;
- the approved Git publication identity is the only intentional personal
  identity;
- the technical secret pass has no unexplained result;
- historical filenames, messages, and objects pass the same checks as `HEAD`;
- a fresh clone from the publication remote reproduces the cleared result.

Report exceptions explicitly. “Looks generic” and “not a secret” are not
exceptions and are not sufficient evidence for publication.
