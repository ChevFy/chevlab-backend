from fastapi import APIRouter, Depends, HTTPException, status

from modules.auth.jwt import get_current_user

from .service import DockerException, read_containers

docker_router = APIRouter(prefix="/api", tags=["docker"])


@docker_router.get(
    "/containers",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(get_current_user)],
)
def get_containers():
    try:
        return read_containers()
    except DockerException:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Docker daemon unavailable",
        ) from None
