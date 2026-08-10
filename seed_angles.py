"""Seed the six confirmed messaging angles via src/dedupe.py's angle CRUD (not raw SQL).
Idempotent: checks existing slugs first via dedupe.get_angles(), so re-running never
duplicates - re-run is a no-op for any slug already present.

loose_skin's body_area is left empty deliberately - not yet confirmed with the team, do
not guess. default_realism/includes_product weren't specified for it either, so they're
left at the neutral column default rather than invented.
"""
from src import dedupe

ANGLES = [
    {
        "name": "Crepey Skin", "slug": "crepey_skin",
        "body_area": "elbow and forearm", "default_realism": "high_spec_studio",
        "includes_product": True, "notes": "",
    },
    {
        "name": "Menopause", "slug": "menopause",
        "body_area": "collarbone or forearm", "default_realism": "high_spec_studio",
        "includes_product": True, "notes": "",
    },
    {
        "name": "GLP-1", "slug": "glp1",
        "body_area": "none (educational diagram)", "default_realism": "illustrated",
        "includes_product": False, "notes": "",
    },
    {
        "name": "Bruising", "slug": "bruising",
        "body_area": "forearm", "default_realism": "ugc_native",
        "includes_product": True, "notes": "",
    },
    {
        "name": "Sun Damage", "slug": "sun_damage",
        "body_area": "shoulder and upper back", "default_realism": "ugc_native",
        "includes_product": True, "notes": "",
    },
    {
        "name": "Loose Skin", "slug": "loose_skin",
        "body_area": "", "default_realism": "", "includes_product": True,
        "notes": "body_area not yet confirmed with the team - left empty rather than "
                 "guessed. default_realism was not specified for this angle either.",
    },
]


def main():
    dedupe.init_angles()
    dedupe.init_angle_language()
    existing_slugs = {a["slug"] for a in dedupe.get_angles()}
    for angle in ANGLES:
        if angle["slug"] in existing_slugs:
            print(f"skip (already exists): {angle['slug']}")
            continue
        new_id = dedupe.add_angle(
            angle["name"], angle["slug"],
            body_area=angle["body_area"], default_realism=angle["default_realism"],
            includes_product=angle["includes_product"], notes=angle["notes"],
        )
        print(f"added: {angle['slug']} (id={new_id})")


if __name__ == "__main__":
    main()
