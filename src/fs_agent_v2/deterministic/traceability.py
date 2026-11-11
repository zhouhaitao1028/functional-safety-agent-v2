from __future__ import annotations


class TraceabilityValidator:
    """Validate references and mandatory coverage without model judgement."""

    def validate(self, artifact: dict, known_ids: set[str]) -> dict:
        dangling: list[dict] = []
        incoming: dict[str, int] = {item: 0 for item in known_ids}
        duplicate_links: list[dict] = []
        seen: set[tuple[str, str, str]] = set()

        for link in artifact.get("links", []):
            key = (link["source_id"], link["target_id"], link["type"])
            if key in seen:
                duplicate_links.append(link)
            seen.add(key)
            for field in ("source_id", "target_id"):
                if link[field] not in known_ids:
                    dangling.append({"field": field, "id": link[field], "link": link})
            if link["target_id"] in incoming:
                incoming[link["target_id"]] += 1

        required_targets = set(artifact.get("required_target_ids", []))
        missing_coverage = sorted(item for item in required_targets if incoming.get(item, 0) == 0)
        return {
            "valid": not dangling and not duplicate_links and not missing_coverage,
            "dangling_references": dangling,
            "duplicate_links": duplicate_links,
            "missing_coverage": missing_coverage,
            "link_count": len(artifact.get("links", [])),
        }

