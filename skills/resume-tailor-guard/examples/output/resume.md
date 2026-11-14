# Jordan Avery

Denver, CO | (303) 555-0142 | jordan.avery@example.com | https://jordan-avery.example | https://git.example/jordan-avery

## Summary

Data engineer with **5 years** of professional experience in software and data engineering, most recently on shipment, carrier and billing pipelines at a logistics company. Built and maintained **14 Apache Airflow DAGs** that load data from 6 source systems into Snowflake every night. Migrated 38 finance models to dbt and added Great Expectations checks to the 5 highest-traffic tables.

## Skills

- **Languages:** Python, SQL, Bash
- **Data:** Apache Airflow, dbt, Snowflake, PostgreSQL, Great Expectations
- **Cloud and tooling:** AWS, Amazon S3, Docker, GitHub Actions, Git, pytest
- **Practices:** Data quality testing, On-call support

## Experience

### Quillmere Logistics | Denver, CO
*Data Engineer* | Jun 2021 – Aug 2024

- Built and maintained **14 Apache Airflow DAGs** that load shipment, carrier and billing data from 6 source systems into Snowflake every night.
- Added Great Expectations checks to the 5 highest-traffic tables, which caught **23 bad loads** in their first year before analysts saw them.
- Cut the nightly load window from 4 hours to **2.5 hours** by partitioning the 2 largest tables and running independent DAGs in parallel.
- Migrated 38 finance models from hand-written SQL scripts to **dbt**, with tests on every model. Monthly close data preparation went from 3 days to 1 day.

### Pine Hollow Credit Union | Boise, ID
*Software Developer* | Jul 2019 – May 2021

- Rewrote the nightly statement export as a set of SQL jobs on PostgreSQL, cutting its run time from 95 minutes to **20 minutes**.
- Built a reconciliation report that compares ledger totals with the core banking export and flags mismatches over $50.

## Projects

### [gtfs-delay-map](https://git.example/jordan-avery/gtfs-delay-map) | 2023

- Built a Python tool that reads public GTFS-Realtime transit feeds and maps average bus delays by route and hour, processing about **300,000 records per day**. Packaged it with Docker and a GitHub Actions workflow.

## Education

### Westmarch Institute of Technology | Fort Collins, CO
*B.S. in Computer Science* | Sep 2015 – May 2019
