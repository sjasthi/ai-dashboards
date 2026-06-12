# AI-Dashboards

## Description

An AI-powered dashboard system that ingests one or more CSV or Excel files,
analyzes them, and produces visualizations, reports, summaries, and recommendations.
Reports can be downloaded as PDF/HTML or sent via email.

## Requirements

- Python 3.14+
- [Poetry](https://python-poetry.org/docs/#installation)

## Key Dependencies

| Package     | Purpose                          |
|-------------|----------------------------------|
| numpy       | Numerical computation            |
| pandas      | Data loading and transformation  |
| matplotlib  | Charting and visualizations      |
| kagglehub   | Kaggle dataset downloads         |

## Setup

1. Clone the repo
```bash
   git clone https://github.com/yourname/ai-dashboards.git
   cd ai-dashboards
```

2. Install Poetry (if you haven't already)
```bash
   pip install poetry
```

3. Install dependencies
```bash
   poetry install
```

4. (Optional) Set up Kaggle credentials for dataset downloads
   See https://www.kaggle.com/docs/api for instructions.

5. Run the app
```bash
   poetry run python app/main.py
```
```bash
   poetry run python python/data_main.py
```

## Running Tests

```bash
poetry run pytest
```

## Adding Dependencies

```bash
poetry add package-name
```