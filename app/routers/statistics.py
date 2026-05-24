from fastapi import APIRouter

from app.models import StatisticsResponse
from app.repository import StatisticsRepository
from app.services.radio_stats import get_noise_floor_history

router = APIRouter(prefix="/statistics", tags=["statistics"])


@router.get("", response_model=StatisticsResponse)
async def get_statistics() -> StatisticsResponse:
    data = await StatisticsRepository.get_all()
    data["noise_floor_24h"] = get_noise_floor_history()
    return StatisticsResponse(**data)
