"""Push the local roast prompt to Phoenix and tag it "production".

Usage:
    python -m src.scripts.seed_prompts

Each run creates a new Phoenix version; the tagged one is what the app
serves (see get_active_prompt).
"""

from phoenix.client import Client
from phoenix.client.types import PromptVersion

from src.agent.system_prompts import (
    LOCAL_PROMPT,
    PHOENIX_PROMPT_NAME,
    PHOENIX_PROMPT_TAG,
)
from src.config import settings


def seed() -> None:
    client = Client(base_url=settings.PHOENIX_CLIENT_ENDPOINT)
    print(f"Pushing v{LOCAL_PROMPT.version} ({LOCAL_PROMPT.label})...")
    prompt = client.prompts.create(
        name=PHOENIX_PROMPT_NAME,
        version=PromptVersion(
            [{"role": "system", "content": LOCAL_PROMPT.prompt}],
            model_name=settings.GEMINI_MODEL,
        ),
        prompt_description=f"v{LOCAL_PROMPT.version}: {LOCAL_PROMPT.label}",
    )
    version_id = str(prompt.id)
    print(f"Tagging {version_id} as '{PHOENIX_PROMPT_TAG}'...")
    client.prompts.tags.create(
        prompt_version_id=version_id,
        name=PHOENIX_PROMPT_TAG,
        description="Current production prompt version",
    )
    print("Done!")


if __name__ == "__main__":
    seed()
