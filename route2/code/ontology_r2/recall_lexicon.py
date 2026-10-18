"""Offline lexicon and slots for the existing recall path.

These tables are compiled from structural members and relations. They do not
score APIs or cards.
"""


def build_recall_artifacts(data, members, structural):
    lexicon = []
    seen = set()
    for member in members.get("members", []):
        surfaces = [*member.get("names", []), *member.get("aliases", [])]
        for surface in surfaces:
            key = (surface, member.get("dimension_code"), member.get("member_code"))
            if not surface or key in seen:
                continue
            seen.add(key)
            lexicon.append({
                "surface": surface,
                "kind": "dimension_member",
                "dimension_code": member.get("dimension_code"),
                "member_code": member.get("member_code"),
                "level": member.get("level"),
            })
    slots = []
    for link in structural.get("links") or []:
        slots.append({
            "source_table": link["source_table"],
            "source_column": link["source_column"],
            "target_table": link["target_table"],
            "target_column": link["target_column"],
            "relation": link["parent"],
            "name": link.get("predicate_name"),
        })
    for link in structural.get("column_links") or []:
        slots.append({
            "source_table": link["table"],
            "source_column": link["code_column"],
            "target_table": link["table"],
            "target_column": link["other_column"],
            "relation": link["kind"],
            "name": link["kind"],
        })
    return {"lexicon": lexicon, "slots": slots}
