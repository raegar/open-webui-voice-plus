from open_webui.utils.videos.portrait import (
    PORTRAIT_TEXT_ENCODER,
    PORTRAIT_UNET,
    PORTRAIT_VAE,
    build_portrait_prompt,
    build_portrait_workflow,
    find_image_output,
)


def test_portrait_uses_the_measured_z_image_settings():
    workflow = build_portrait_workflow("a portrait", 42)
    sampler = workflow["3"]["inputs"]
    assert (sampler["steps"], sampler["cfg"], sampler["sampler_name"], sampler["scheduler"]) == (
        8,
        1,
        "res_multistep",
        "simple",
    )
    assert sampler["seed"] == 42
    assert workflow["28"]["inputs"]["unet_name"] == PORTRAIT_UNET
    assert workflow["30"]["inputs"] == {
        "clip_name": PORTRAIT_TEXT_ENCODER,
        "type": "lumina2",
        "device": "default",
    }
    assert workflow["29"]["inputs"]["vae_name"] == PORTRAIT_VAE
    assert workflow["11"]["inputs"]["shift"] == 3
    assert (workflow["13"]["inputs"]["width"], workflow["13"]["inputs"]["height"]) == (832, 1216)
    # The negative is the positive zeroed out, as in ComfyUI's own template.
    assert workflow["33"]["inputs"]["conditioning"] == ["27", 0]
    assert sampler["negative"] == ["33", 0]
    assert workflow["9"]["class_type"] == "SaveImage"


def test_portrait_prompt_describes_the_look_without_a_name_or_style():
    prompt = build_portrait_prompt("a man in a grey wool overcoat.")
    assert "a man in a grey wool overcoat." in prompt
    assert prompt.startswith("Realistic photograph")
    assert "plain light grey background" in prompt


def test_image_output_is_read_from_the_save_node():
    entry = {"outputs": {"9": {"images": [{"filename": "owui-gm-portrait_00001_.png", "subfolder": ""}]}}}
    assert find_image_output(entry)["filename"] == "owui-gm-portrait_00001_.png"
    assert find_image_output({"outputs": {}}) is None


def test_the_prompt_writer_gets_the_character_and_any_direction():
    from open_webui.utils.videos.portrait import build_portrait_writer_messages

    messages = build_portrait_writer_messages(
        "Marek", "the landlord's nephew", "a man in a grey wool overcoat", "more menacing"
    )
    system, user = messages[0]["content"], messages[1]["content"]
    # Reference rules always apply; the treatment follows the subject.
    assert "whole subject is clearly visible" in system
    assert "never make it look ordinary or human unless it is" in system
    assert user.splitlines() == [
        "Character: Marek",
        "Who they are: the landlord's nephew",
        "How they look: a man in a grey wool overcoat",
        "The player's direction for this portrait, which takes priority over the look "
        "where they conflict: more menacing",
    ]
    assert "direction" not in build_portrait_writer_messages("Marek", "", "tall")[1]["content"]


def test_written_prompts_are_cleaned_to_the_prompt_alone():
    from open_webui.utils.videos.portrait import clean_portrait_prompt

    raw = '<think>plan it</think>\n**Prompt:** "A towering figure of\n  grey static, mid-shudder."'
    assert clean_portrait_prompt(raw) == "A towering figure of grey static, mid-shudder."
    assert clean_portrait_prompt("```\nA lone lantern spirit.\n```") == "A lone lantern spirit."
    assert clean_portrait_prompt("Plain prompt.") == "Plain prompt."
