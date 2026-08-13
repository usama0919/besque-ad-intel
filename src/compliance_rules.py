"""Shared compliance guardrails for BOTH image and copy generation.

Single source of truth so the rule wording can never drift between
src/generate_image_prompt.py and src/generate_copy.py. From the marketing
team's own pre-flight checklist - not style preferences, legal/compliance
requirements.

IMPORTANT - enforcement is uneven and that is a known, accepted gap, not an
oversight: C1 (real people), C4 (shaming body framing), C6 (sexualized
framing), and C7 (weight/treatment text in-image) are prompt-only on the image
side. There is no mechanical check that verifies a generated image actually
honours them - only C2 (fabricated testimonials/claims), C3/C8 (numeric,
efficacy, and ingredient/formulation claims), and C9 (borrowed personal
attribution/account identity - the personal-name/handle-in-TEXT half only),
on the copy side, have a mechanical backstop (see src/compliance.py). C9's
account-chrome half (an avatar's face, or a display name/handle rendered as
part of reproduced UI chrome rather than as copy text) has NO mechanical
backstop at all - same as C1 - since it is pixels, not a string a regex can
scan; it is prompt-only plus the critic checklist, exactly like C1. If the
image model ignores these instructions, nothing here catches it -
output_critic.py's checklist is the only post-hoc backstop for this category
(see its own WEIGHT/TREATMENT TEXT entry, its ingredient/formulation entry for
C8, and its borrowed-attribution/account-identity entry for C9).
"""

COMPLIANCE_RULES = (
    "BESQUE COMPLIANCE RULES (STRICT - apply regardless of what the source/competitor ad does): "
    "C1. NO REAL PEOPLE: any person depicted or described must be a fictional, generated model - "
    "never the likeness, face, body, hairstyle, or other identifying features of a real or specific "
    "individual, including anyone shown in the competitor's source ad. Render any implied person as "
    "a generic, non-identifiable stand-in only. "
    "C2. NO FABRICATED TESTIMONIALS: never invent a customer quote, review, star rating, or "
    "first-person endorsement that is not drawn from supplied APPROVED TESTIMONIALS material. A "
    "quoted or first-person testimonial is permitted only when it is a real, consented customer "
    "story supplied in APPROVED TESTIMONIALS below - adapt its language and angle freely, but never "
    "invent a testimonial where none is supplied, and never alter a supplied testimonial's substance. "
    "Never invent a quantified outcome or statistic (e.g. \"94% saw results\") unless it is explicitly "
    "supplied in APPROVED CLAIMS. "
    "C3. NO OVERCLAIMING: do not use \"clinically proven\", \"clinically tested\", \"doctor "
    "recommended\", \"guaranteed\", \"replaces surgery/Botox/filler\", or similar substantiation-"
    "implying language unless that substantiation is explicitly supplied as an approved claim. "
    "Descriptive, non-comparative claims about how the product feels or what it is formulated to do "
    "(e.g. \"improves skin texture\", \"deeply hydrating\") are acceptable. "
    "C4. NO SHAMING OR HUMILIATING BODY FRAMING: do not depict or describe a \"before\" state, or any "
    "body, using mockery, disgust, exaggerated sadness, or humiliation. Soften any such framing found "
    "in the source ad to a neutral, respectful, non-judgmental tone. "
    "C5. NO IMPLIED MEDICAL/PHARMACEUTICAL CLAIMS: do not imply the Besque product is a drug, a "
    "prescription medication, or a medical treatment, and do not claim or imply it is a substitute or "
    "replacement for one - including GLP-1 medications. Referencing GLP-1, or the skin effects of "
    "GLP-1 use, as context for a skin concern is an approved messaging angle and is NOT itself a "
    "violation. The line is: the product may be positioned as addressing a skin concern GLP-1 use is "
    "associated with; it may never be positioned as acting like, replacing, or medically treating the "
    "way a prescription drug would. "
    "C6. NO SEXUALIZED FRAMING: do not depict or describe any person in a sexualized pose, camera "
    "framing/angle, or narrative context (e.g. framed for titillation rather than skincare "
    "application), regardless of how the competitor's source ad is styled. Depicting bare skin or "
    "body areas relevant to the product's use (e.g. legs, torso, underarms) is expected for a "
    "body-oil application context and is NOT itself a violation - this rule targets sexualization of "
    "pose/framing/narrative intent, not depiction of skin or body areas. "
    "C7. NO WEIGHT OR TREATMENT TEXT IN-IMAGE: never render, as in-image text (a headline, caption, "
    "label box, callout, or any other on-image typography, in ANY zone or container), a numeric "
    "weight value with its unit (e.g. \"170 lbs\", \"82kg\"), weight-change or before/after-weight "
    "language (e.g. a \"Start\"/\"Finished\"/\"Rebound\" figure, \"lost 40lbs\"), or a named "
    "pharmaceutical/treatment term (GLP-1, Ozempic, Wegovy, semaglutide, tirzepatide, injection, "
    "shot) - even when the competitor's reference ad shows this text, and even on an approved "
    "GLP-1-context messaging angle (see C5: the angle may be referenced in writing, but a specific "
    "weight figure or drug/treatment brand name rendered as in-image text is never permitted, on "
    "any ad). This applies regardless of which zone the text sits in, including a zone with no "
    "Besque replacement wording assigned to it: such a zone is REMOVED, never left showing the "
    "reference's original weight or treatment text by default. "
    "C8. NO UNSUBSTANTIATED INGREDIENT OR FORMULATION CLAIMS: do not state or imply what the "
    "product is made of, contains, or is formulated with (e.g. \"formulated with natural "
    "ingredients\", \"made with organic ingredients\", \"natural formula\", \"contains active "
    "ingredients\") unless that specific ingredient fact is explicitly supplied as an approved "
    "claim. This is a DIFFERENT category from C3's own exception for descriptive, non-comparative "
    "claims about what the product FEELS like or DOES (\"improves skin texture\", \"deeply "
    "hydrating\" remain acceptable under C3, unchanged) - C3's exception was never meant to cover, "
    "and does not cover, a claim about what the product is COMPOSED of. Applies identically while "
    "products.hero_claim is blank and once it is populated: with no ingredient fact supplied, no "
    "ingredient or formulation claim may be asserted from any source - a generic industry-standard "
    "phrase, the competitor reference ad's own ingredient claims, or an invented one. In BOTH copy "
    "and in-image text. "
    "C9. NO BORROWED PERSONAL ATTRIBUTION OR ACCOUNT IDENTITY: any personal name, initial-surname "
    "construction (e.g. \"Sean R.\"), signature, social media @handle or username (e.g. "
    "\"@fitness_ty\"), profile/display name, or other account identifier appearing in the reference "
    "ad's own text, testimonial attribution, account chrome (an avatar/handle/name card on a "
    "UGC-style reference), or any other reference-derived field must never appear in Besque's "
    "output, in copy or in-image text, EVEN when the surrounding brand and product references were "
    "correctly substituted. This is a DIFFERENT category from C2: C2 governs a FABRICATED "
    "testimonial's content; this governs a BORROWED identity - a real individual's name, handle, or "
    "account carried over from someone else's ad into a Besque ad publishes an unconsented "
    "endorsement, a legal exposure independent of whether the testimonial content itself is genuine "
    "or well-written, and a handle is directly traceable to a real account, exactly as much a real "
    "identity as a name is. Where a UGC-style reference's layout includes account chrome (avatar, "
    "handle, verified tick, follow button), the CHROME may be reproduced as layout, but its CONTENT "
    "must be Besque's own account identity or REMOVED - never the competitor's or any other real "
    "account's identity, and an avatar's face is a depicted person like any other: it is bound by C1 "
    "and rule 10 exactly the same as the ad's primary subject, never treated as decorative UI "
    "furniture exempt from either. The ONLY personal attribution ever permitted is the one supplied "
    "in APPROVED TESTIMONIALS below (a real, consented Besque customer) - an unrecognised name, "
    "handle, or account identifier found in reference-derived material is REMOVED by default, never "
    "carried over on the assumption it is harmless (the same default C7 already applies to an "
    "ungoverned zone)."
)
