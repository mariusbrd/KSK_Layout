# HR Dashboard

## Overview

This project is a Streamlit-based HR Dashboard for workforce analytics, target-vs-actual analysis, and forecast-driven workforce planning.

The application combines:

- current-state workforce analysis
- target-vs-actual plan position analysis
- attrition forecasting
- hiring forecasting
- combined net workforce forecasting
- forward simulation of workforce development
- exclusion and data-quality controls
- bilingual UI support (German / English)

The dashboard is designed to work with structured HR source files when available and to remain usable with reproducible synthetic fallback data when source data is incomplete or missing.

## Key Features

- Compact workforce dashboard for current-state and current-vs-target analysis
- Dedicated `Köpfe` / `MAK` / `EUR` view switching
- Detailed `Ist vs Soll` analysis, including matrix and detail views
- Attrition forecast for partial retirement, retirement, resignations, and dormant-status effects
- Hiring forecast for apprentices, trainees, and external hires
- Hybrid forecast combining attrition and hiring into a single net view
- Compact plus Simulation page for projecting the workforce to a future date and reusing the compact analysis on the simulated state
- Global sidebar filters for time, organization, job families, cohorts, demographics, employment, qualifications, and partial-retirement status
- Exclusion-group management that affects analytical scope across the dashboard
- Synthetic test-data fallback for development, demos, and environments without source files
- German / English language switching across the UI

## Dashboard Pages

### Kompakt / Compact

The compact page is the main analytical cockpit for current-state reporting. It focuses on:

- current-state analysis (`IST`)
- current-vs-target analysis (`IST vs SOLL`)
- KPI cards, charts, and management summaries
- target-vs-actual analysis for heads, FTE-style capacity (`MAK`), and cost views

The `Köpfe: Ist vs Soll` view is especially important for plan-position analysis. It compares:

- planned pay grade of the position
- actual pay grade of the assigned employee
- exact matches
- in-band matches
- overgrading / undergrading
- vacant positions
- special cases such as technical additional positions and missing target grades

### Kompakt plus Simulation / Compact plus Simulation

This page projects the current workforce to a selected future date using the existing attrition and hiring logic, then reuses the compact analysis on the simulated snapshot.

It is useful for:

- scenario walkthroughs
- future-state KPI analysis
- target-vs-actual checks on projected staffing structures

### Forecast: Attrition

The attrition page models workforce losses and capacity effects over time. It includes:

- configurable forecast horizon
- driver controls for partial retirement, retirement, resignations, and dormant-status behavior
- overview trends
- driver breakdowns
- export-oriented detail tables

### Forecast: Hiring

The hiring page models additions to the workforce through:

- apprentices
- trainee programs
- external hiring

It includes parameter controls, distribution logic, overview charts, cluster views, details, and cost analysis.

### Forecast: Hybrid

The hybrid page combines attrition and hiring into a single net workforce view. It supports:

- unified forecast horizon
- combined summary KPIs
- net headcount and capacity trajectory
- driver-level views for inflows and outflows
- list/detail sections for both sides of the forecast

### Settings

The settings page provides configuration and setup utilities, including:

- data-source status
- upload-based data sourcing
- clustering configuration
- salary and employer-cost settings
- prepared dashboard configuration

### Exclusion Groups

The exclusion page provides transparent controls for exclusion logic that affects the analytical scope. It helps validate which groups are currently excluded and how this impacts downstream views.

## Data Inputs

The dashboard expects HR-oriented input data with schemas compatible with the current loader and page logic. In practice, this includes source data for areas such as:

- employees
- plan positions
- partial-retirement status/details
- training / apprenticeship data

The application reads and prepares these datasets through the centralized data layer in [`dataloader/`](./dataloader).

### Data Preparation Layer

The central preparation pipeline:

- loads raw or uploaded data
- normalizes important identifiers
- assigns derived attributes such as cohorts, status classes, and cost/capacity fields
- applies clustering and exclusions
- prepares page-ready snapshots and history data

This logic is intentionally shared so that the pages operate on a consistent prepared-data layer.

## Synthetic Fallback / Test Data

When original source files are incomplete or unavailable, the dashboard can fall back to synthetic data generation.

The synthetic datasets are designed to be:

- schema-compatible with the dashboard
- reproducible
- analytically meaningful rather than purely random

The synthetic generator includes realistic HR patterns such as:

- occupied and vacant positions
- regular and technical/additional positions
- meaningful target-vs-actual pay-grade mismatches
- non-trivial distributions across organization, job family, age, qualification, and employment patterns

This is especially useful for:

- local development
- UI validation
- test automation
- runtime fallback in environments without source files

## Sidebar, Filters, and Language

The dashboard uses a shared sidebar control system across pages.

Core capabilities include:

- page navigation
- data-status display
- active selection summary
- metric switching (`Köpfe`, `MAK`, `EUR`)
- global filtering
- page-specific contextual hints
- language switching between German and English

The language switcher is backed by a central i18n layer in [`utils/i18n.py`](./utils/i18n.py). User-facing text is translated at the display layer so analytical logic remains independent from UI language.

## Project Structure

High-level structure:

- [`app.py`](./app.py)
  Main Streamlit entry point and page navigation setup
- [`pages/`](./pages)
  Dashboard pages
- [`components/`](./components)
  Shared UI components such as sidebar, shell, cards, and setup helpers
- [`dataloader/`](./dataloader)
  Data loading, preparation, synthetic fallback, clustering, and compact simulation engine
- [`abgaenge/`](./abgaenge)
  Attrition forecast logic, parameters, and related helpers
- [`zugaenge/`](./zugaenge)
  Hiring forecast logic, parameters, and enrichment helpers
- [`utils/`](./utils)
  Shared helpers for i18n, formatting, plotting, caching, settings, and normalization
- [`config/`](./config)
  Settings, static configuration, and constants
- [`tests/`](./tests)
  Regression, i18n, rendering, and analytical test coverage

## Requirements

The project currently depends on:

- Python 3.11+
- Streamlit
- pandas
- numpy
- plotly
- openpyxl
- xlsxwriter
- faker
- python-dateutil

See [`requirements.txt`](./requirements.txt) for the current dependency list.

## Local Setup

### 1. Create and activate a virtual environment

Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Start the dashboard

```powershell
streamlit run app.py
```

Streamlit will print a local URL for the dashboard.

## Running the Dashboard

Typical local workflow:

1. Start the app with `streamlit run app.py`
2. Open the dashboard in the browser
3. Use the sidebar to:
   - navigate between pages
   - switch the metric view
   - set global filters
   - switch language
4. If source data is not available, verify that the application has switched to synthetic fallback data

## Testing and Validation

The repository contains an extensive test suite covering, among other things:

- data preparation regression
- forecast golden-master behavior
- runtime page rendering
- i18n coverage
- compact simulation behavior
- sidebar and layout behavior

Run the test suite with:

```powershell
py -3 -m pytest tests -q
```

For quicker targeted checks, run individual modules such as:

```powershell
py -3 -m pytest tests\test_pages_phase3_i18n.py tests\test_pages_phase4_i18n.py tests\test_pages_phase5_i18n.py -q
```

## Notes

- The application contains both business-facing pages and technical helper/debug paths.
- Forecast pages are designed to reuse shared prepared data and shared filter state wherever possible.
- Some helper files and scripts still contain legacy naming from earlier project phases, but the active dashboard is documented here neutrally as an HR Dashboard.

## Project Status

This repository contains an actively maintained dashboard application with:

- current-state analytics
- forecast modules
- synthetic fallback capability
- automated regression coverage
- bilingual UI support

It is suitable for local development, internal technical review, and structured dashboard operation in environments where compatible HR source data is available.
