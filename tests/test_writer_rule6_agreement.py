"""Part C: the Claude prompt-writer's instructions (generate_image_prompt_writer.
_build_user_prompt) and brand_rules()'s mechanical rule 6/7 must always agree on what
text/product content is permitted. A real failure showed why this matters: the writer
described text and a product count that rule 6/7 then forbade, and Gemini discarded the
whole composition rather than reconciling the contradiction - there must be no state
where one describes/permits something the other forbids."""
from src import generate_image_prompt, generate_image_prompt_writer


def test_text_in_image_true_writer_and_rule6_agree_on_exact_headline():
    headline = "Firmer Skin By Friday"
    subtext = "7 cold-pressed oils"

    writer_prompt = generate_image_prompt_writer._build_user_prompt(
        {}, text_in_image=True, headline=headline, subtext=subtext
    )
    rule6 = generate_image_prompt._rule6_text_policy(text_in_image=True, headline=headline, subtext=subtext)

    # Both must name the exact same headline/subtext text, quoted - not a paraphrase.
    assert f'"{headline}"' in writer_prompt
    assert f'"{headline}"' in rule6
    assert f'"{subtext}"' in writer_prompt
    assert f'"{subtext}"' in rule6

    # The writer must describe it as typography to render; rule 6 must permit exactly that.
    assert "in-scene typography" in writer_prompt
    assert "in-scene typography" in rule6
    assert "ONLY text permitted" in rule6

    # Neither may claim "no text at all" in this mode.
    assert "RESERVED NEGATIVE SPACE" not in writer_prompt
    assert "NEVER render any headline" not in rule6


def test_text_in_image_false_writer_and_rule6_agree_on_no_text():
    writer_prompt = generate_image_prompt_writer._build_user_prompt({}, text_in_image=False)
    rule6 = generate_image_prompt._rule6_text_policy(text_in_image=False)

    # The writer must reserve space and never describe typography...
    assert "RESERVED NEGATIVE SPACE" in writer_prompt
    assert "NO typography" in writer_prompt
    # ...matching rule 6's blanket ban on rendering any headline/marketing text.
    assert "NEVER render any headline" in rule6
    assert "the ONLY text permitted anywhere in the image" in rule6

    # Neither may permit rendering typography in this mode.
    assert "in-scene typography" not in writer_prompt
    assert "TEXT-IN-IMAGE MODE" not in rule6


def test_text_in_image_true_without_headline_writer_and_rule6_both_fall_back_to_no_text():
    """text_in_image=True but headline=None (e.g. copy generation produced none) - the
    writer and rule 6 are two independent functions with no shared state; both must
    independently fall back to the SAME "no text" behaviour rather than one permitting
    text the other forbids."""
    writer_prompt = generate_image_prompt_writer._build_user_prompt({}, text_in_image=True, headline=None)
    rule6_with_flag_no_headline = generate_image_prompt._rule6_text_policy(text_in_image=True, headline=None)
    rule6_default = generate_image_prompt._rule6_text_policy(text_in_image=False)

    assert "RESERVED NEGATIVE SPACE" in writer_prompt
    assert "NO typography" in writer_prompt
    # Falls back to character-for-character the same rule 6 text as text_in_image=False.
    assert rule6_with_flag_no_headline == rule6_default


# ---- Bonus: the analogous check for include_product/rule 7, same root-cause class as
# the text_in_image bug (Part A's "two bottles" failure was a rule-7 disagreement) ----

def test_include_product_true_writer_and_rule7_agree_on_exactly_one():
    writer_prompt = generate_image_prompt_writer._build_user_prompt({}, include_product=True)
    rule7 = generate_image_prompt._rule7_product_policy(include_product=True)
    assert "EXACTLY ONE Besque" in writer_prompt
    assert "the ONLY product permitted" in rule7
    assert "PRODUCTLESS" not in writer_prompt
    assert "PRODUCTLESS" not in rule7


def test_include_product_false_writer_and_rule7_agree_on_none():
    writer_prompt = generate_image_prompt_writer._build_user_prompt({}, include_product=False)
    rule7 = generate_image_prompt._rule7_product_policy(include_product=False)
    assert "PRODUCTLESS" in writer_prompt
    assert "PRODUCTLESS" in rule7
    assert "EXACTLY ONE Besque" not in writer_prompt
