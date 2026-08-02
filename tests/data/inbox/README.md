# Inbox — drop spreadsheets here to have them tested

Got a file you want checked? Put it in this folder and run the tests. No naming
convention, no code change, no manifest edit.

```
pytest tests/test_inbox_files.py -v
python scripts/run_test_plan.py --only inbox
```

## What it accepts

| Accepted | `.csv`, `.xls`, `.xlsx` |
|---|---|
| Ignored | anything else — listed in the report so a typo is visible, never silently dropped |

## How files are grouped

- **A loose file** at the top of `inbox/` is tested on its own.
- **A subfolder** is tested as one batch — every supported file in it is uploaded
  together, the way a user would multi-select them. Mixing extensions in a
  subfolder is fine and is the main reason to use one.
- One level of nesting. A directory that directly contains supported files is a
  batch; deeper nesting is not walked.

```
inbox/
  weird_export.xlsx          -> one case, on its own
  broken_client_data/        -> one case, all three files as a single batch
    orders.csv
    customers.xlsx
    legacy.xls
```

## What gets checked

The same correctness invariants the main suites use — the file reads, it loads,
and above all **the row count the upload screen shows equals the row count the
analysis actually loads**. That last one is the whole reason `workbook_probe`
exists, and it is the check most likely to catch a real problem.

Not checked here: how many workbooks or sheets the file "should" have. You
haven't declared that, and this folder deliberately doesn't ask you to. If you
want those assertions, the file belongs in `tests/data/workbooks/` instead —
see below.

## Nothing here is tracked by git

`.gitignore` excludes everything in this folder except this README. Drop in a
300 MB export or a client's private data without worrying about it reaching
GitHub.

Two things follow from that:

- Recorded expectations for inbox files go to `inbox/baseline.json`, which is
  also ignored. They must never enter the committed `tests/data/baseline.json`,
  which may only describe files everyone actually has.
- An empty inbox is normal — on any machine but yours it always is. The tests
  skip rather than fail, and `run_test_plan.py --strict` does not demand it.

## Promoting a file into the permanent corpus

If a file proves worth keeping, move it out of here:

- **`tests/data/extensions/<csv|xls|xlsx>/`** — for extension coverage. One
  file, single sheet.
- **`tests/data/workbooks/<xls|xlsx>/<cell>/<example>/`** — for the workbook
  matrix, where `<cell>` is `1wb-1sheet`, `2+wb-1sheet`, `1wb-multisheet` or
  `2+wb-multisheet`, and `<example>` is a folder holding that one batch. The
  cell name is the assertion, so put the file in the folder that describes it.

Then run `python scripts/run_test_plan.py --record`, review the `baseline.json`
diff, and commit the file along with it.
