from chatdba.db.metadata_router import MetadataRouter
from chatdba.db.mysql_collector import MysqlEvidenceCollector, MysqlTableTarget
from chatdba.domain.models import EvidenceEnvelope, EvidenceStatus


class RoutedMysqlEvidenceCollector:
    def __init__(self, *, router: MetadataRouter, connection_factory) -> None:
        self._router = router
        self._connection_factory = connection_factory

    def collect(
        self,
        sql: str,
        tables: list[MysqlTableTarget],
    ) -> EvidenceEnvelope:
        route_envelope = self._router.resolve(tables)
        if route_envelope.route is None:
            return route_envelope

        client = self._connection_factory.create_client(route_envelope.route)
        collector = MysqlEvidenceCollector(client)

        explain_json = None
        create_tables: dict[str, str] = {}
        missing_evidence: list[str] = []
        collection_errors: list[str] = []

        try:
            explain_json = collector.collect_explain_json(sql)
        except Exception as exc:
            missing_evidence.append("explain_json")
            collection_errors.append(f"Failed to collect execution plan: {exc}")

        try:
            create_tables = collector.collect_create_tables(tables)
        except Exception as exc:
            missing_evidence.append("create_table")
            collection_errors.append(f"Failed to collect table DDL: {exc}")

        if explain_json is not None and create_tables:
            return EvidenceEnvelope(
                status=EvidenceStatus.FULL,
                route=route_envelope.route,
                explain_json=explain_json,
                create_tables=create_tables,
            )

        if explain_json is None and not create_tables:
            return EvidenceEnvelope(
                status=EvidenceStatus.SQL_ONLY,
                route=route_envelope.route,
                explain_json=None,
                create_tables={},
                missing_evidence=sorted(set(missing_evidence)),
                collection_errors=collection_errors,
            )

        return EvidenceEnvelope(
            status=EvidenceStatus.PARTIAL,
            route=route_envelope.route,
            explain_json=explain_json,
            create_tables=create_tables,
            missing_evidence=sorted(set(missing_evidence)),
            collection_errors=collection_errors,
        )
