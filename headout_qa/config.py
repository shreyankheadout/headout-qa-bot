from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    sunco_base_url: str = "https://headout1663677398.zendesk.com/sc/v2"
    sunco_app_id: str = ""
    sunco_key_id: str = ""
    sunco_key_secret: str = ""
    ultimate_switchboard_id: str = "6a67f54983414eeae9d37a7f"
    booking_field_id: str = "10690136870553"
    email_field_id: str = "10690200241945"

    zendesk_subdomain: str = "headout1663677398"
    zendesk_user_email: str = ""
    zendesk_api_token: str = ""

    sheet_id: str = "1hGrZbcsOjNHNnidsZxp-QdVTTQcSHtOHqz6_HJMyqgA"
    sheet_bookings_gid: int = 0
    sheet_bookings_tab: str = "bookings"
    sheet_scenarios_gid: int | None = None
    sheet_scenarios_tab: str = "scenarios"
    google_sheets_api_key: str = ""
    backup_api_url: str = ""
    backup_api_token: str = ""

    concurrency: int = 50
    message_timeout_seconds: float = 90.0
    escalation_grace_seconds: float = 25.0
    conversation_timeout_seconds: float = 600.0
    max_turns: int = 12
    poll_interval_seconds: float = 1.0

    output_dir: Path = Path("runs")
    llm_provider: str = "scripted"
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""

    server_host: str = "127.0.0.1"
    server_port: int = 8080

    @field_validator("sheet_scenarios_gid", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @property
    def sunco_auth(self) -> tuple[str, str]:
        return (self.sunco_key_id, self.sunco_key_secret)

    @property
    def zendesk_base_url(self) -> str:
        return f"https://{self.zendesk_subdomain}.zendesk.com"

    @property
    def sheet_bookings_export_url(self) -> str:
        # /export?format=csv occasionally 400s right after a sheet is heavily
        # edited (stale export cache) while staying "Anyone with the link" the
        # whole time; /gviz/tq is Google's older but more reliable public-CSV
        # path for the same sheet and doesn't show this failure mode.
        return (
            f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/gviz/tq"
            f"?tqx=out:csv&gid={self.sheet_bookings_gid}"
        )

    @property
    def sheet_bookings_values_url(self) -> str:
        return (
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}"
            f"/values/{self.sheet_bookings_tab}?alt=json"
        )

    @property
    def sheet_scenarios_export_url(self) -> str | None:
        if self.sheet_scenarios_gid is None:
            return None
        return (
            f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/gviz/tq"
            f"?tqx=out:csv&gid={self.sheet_scenarios_gid}"
        )

    @property
    def sheet_edit_url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/edit"

    @property
    def booking_source_label(self) -> str:
        if self.backup_api_url:
            return "backup API"
        if self.google_sheets_api_key:
            return "Sheets v4 API"
        return "CSV export"


@lru_cache
def get_settings() -> Settings:
    return Settings()
