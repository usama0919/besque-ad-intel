"""Shared compliance guardrails for BOTH image and copy generation.

Single source of truth so the rule wording can never drift between
src/generate_image_prompt.py and src/generate_copy.py. From the marketing
team's own pre-flight checklist - not style preferences, legal/compliance
requirements.

IMPORTANT - enforcement is uneven and that is a known, accepted gap, not an
oversight: C1 (real people), C4 (shaming body framing), and C6 (sexualized
framing) are prompt-only on the image side. There is no mechanical check that
verifies a generated image actually honours them - only C2 (fabricated
testimonials/claims), on the copy side, has a mechanical backstop (see
src/compliance.py). If the image model ignores these instructions, nothing
here catches it.
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
    "pose/framing/narrative intent, not depiction of skin or body areas."
)
