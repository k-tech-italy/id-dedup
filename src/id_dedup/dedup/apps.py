from django import apps


class DedupConfig(apps.AppConfig):  # noqa: D101
    name = "id_dedup.dedup"
    verbose_name = "ID deduplication"
