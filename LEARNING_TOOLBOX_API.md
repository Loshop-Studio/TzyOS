# Learning Toolbox API notes

The NovaOS `learning-toolbox` package calls the school API directly from the
tablet. Authentication uses the existing bearer token obtained from
`POST /api/TokenAuth/Login`.

## New evaluation

| Purpose | Method and path |
| --- | --- |
| Task list | `POST /api/services/app/Task/GetStudentTaskListAsync` |
| Task and question groups | `GET /api/services/app/Task/GetExamTaskAsync?id={taskId}` |
| Rendered question, answer and analysis | `GET /Question/View/{questionId}?showAnalysis=true` |
| Exam overview and student grades | `GET /api/services/app/LearningSituations/GetExamOverviewAsync?examId={examId}` |
| Per-question error analysis | `GET /api/services/app/LearningSituations/GetQuestionAnalysisAsync?examId={examId}` |
| Objective-answer workbook | `GET /api/services/app/Exam/ExportObjectiveAnswersAsync?examId={examId}` |

Requests use `Authorization: Bearer <token>`. Overview, analysis and export
also send `AppName: WebClient` and `AppVersion: 0`. The package deliberately
does not implement any share endpoint or share UI.

## Cloud-note source files

Cloud-note metadata and resource responses use the existing daily AES-ECB
encryption scheme. Source downloads follow this sequence:

1. `GET /CloudNotes/api/Resources/GetByFileId?<encrypted fileId query>`
2. Decrypt `data` and read `resourceList`.
3. Ignore resources whose `resourceType` is `2` (thumbnails).
4. Download every remaining `ossImageUrl` as an original source resource.

There is no separate source-file download API in the supplied Apifox project.
The resource URLs returned by `GetByFileId` are the download sources.
