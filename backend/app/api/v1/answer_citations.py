from fastapi import APIRouter, Depends

from app.api.deps import ClientScope, State
from app.api.errors import PROBLEM_RESPONSES
from app.api.schemas.answer_citations import AnswerCitationsData
from app.api.services_impl.answer_citations import ANSWER_CITATIONS
from app.api.v1.common import only_params

router = APIRouter(tags=["results"], responses=PROBLEM_RESPONSES)


@router.get(
    "/clients/{client_id}/answer-citations",
    operation_id="answer_citations",
    response_model=AnswerCitationsData,
    dependencies=[Depends(only_params())],
)
async def answer_citations(state: State, scope: ClientScope) -> AnswerCitationsData:
    return await state.service(ANSWER_CITATIONS).view(scope)
