# Jordan Avery

Denver, CO | (303) 555-0142 | jordan.avery@example.com | https://jordan-avery.example | https://git.example/jordan-avery

September 9, 2024

Dear Sablewick Freight Hiring Team,

Your posting describes carrier data that arrives late and in inconsistent formats, and customers who notice when it does. Nightly loads of that kind of data were my work at Quillmere Logistics, where I built and maintained 14 Apache Airflow DAGs that feed Snowflake from 6 source systems. I would like to do that work on your shipment visibility platform as a Data Engineer.

You ask for data quality checks that catch bad loads before customers see them. The Great Expectations checks I added to the 5 highest-traffic tables at Quillmere caught 23 bad loads in their first year, before analysts saw them. You also want shorter load times. Partitioning the two largest tables and running independent DAGs in parallel took our nightly load window from 4 hours to 2.5 hours. On the modeling side, I moved 38 finance models from hand-written SQL scripts to dbt, with tests on every model.

The posting lists streaming experience as a plus. The closest thing I have done is a three-month pilot that ingested one carrier's tracking events through Apache Kafka into PostgreSQL, about 1.2 million events per day with a median delay under 40 seconds. The pilot was not moved to production. On-call duty is familiar work. I wrote the on-call runbook for Quillmere's data platform and took part in a 4-person on-call rotation.

Before Quillmere I was a Software Developer at Pine Hollow Credit Union. There I rewrote the nightly statement export as a set of SQL jobs on PostgreSQL, which cut its run time from 95 minutes to 20 minutes. I also built a reconciliation report that compares ledger totals with the core banking export. I am based in Denver, where this role is located, and I would welcome a conversation about your carrier feeds and the pipelines behind them. Thank you for your time.

Sincerely,

Jordan Avery
