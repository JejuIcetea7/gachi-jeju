"""주변 숙소 후보의 공개 응답 형식입니다."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .lodgings import MAX_LATITUDE, MAX_LONGITUDE, MIN_LATITUDE, MIN_LONGITUDE


class LodgingCoordinate(BaseModel):
    model_config = ConfigDict(strict=True)

    latitude: float = Field(ge=MIN_LATITUDE, le=MAX_LATITUDE, allow_inf_nan=False)
    longitude: float = Field(ge=MIN_LONGITUDE, le=MAX_LONGITUDE, allow_inf_nan=False)


class LodgingCandidate(LodgingCoordinate):
    provider_place_id: Annotated[str, StringConstraints(pattern=r"^[0-9]{1,40}$")]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    address: str = Field(max_length=300)
    phone: str = Field(max_length=80)
    distance_m: int = Field(ge=0)
    place_url: str

    @model_validator(mode="after")
    def validate_place_url(self):
        if self.place_url != "https://place.map.kakao.com/" + self.provider_place_id:
            raise ValueError("숙소 지도 주소와 장소 식별값이 일치하지 않습니다.")
        return self


class LodgingSearchResponse(BaseModel):
    model_config = ConfigDict(strict=True)

    source: Literal["kakao_local"]
    requested_at: Annotated[AwareDatetime, Field(strict=False)]
    center: LodgingCoordinate
    radius_m: int = Field(ge=1, le=20_000)
    places: list[LodgingCandidate] = Field(max_length=10)

    @model_validator(mode="after")
    def validate_unique_places(self):
        identifiers = [place.provider_place_id for place in self.places]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("숙소 후보의 식별값이 중복됩니다.")
        return self
