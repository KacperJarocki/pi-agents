from pydantic import BaseModel, Field


class WifiConfig(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    psk: str = Field(min_length=8, max_length=63)
    country_code: str = Field(default="PL", min_length=2, max_length=2)
    channel: int = Field(default=6, ge=1, le=165)

    ap_interface: str = Field(default="wlan0", min_length=1)
    upstream_interface: str = Field(default="eth0", min_length=1)

    subnet_cidr: str = Field(default="192.168.50.0/24")
    gateway_ip: str = Field(default="192.168.50.1")
    dhcp_range_start: str = Field(default="192.168.50.100")
    dhcp_range_end: str = Field(default="192.168.50.200")

    enabled: bool = True


class ValidationResult(BaseModel):
    ok: bool
    issues: list[str] = []


class GatewayStatus(BaseModel):
    ap_interface_exists: bool
    upstream_interface_exists: bool
    ap_ip: str | None = None
    ip_forward: bool | None = None
    nat_rule_present: bool | None = None


class ApplyResult(BaseModel):
    ok: bool
    message: str
