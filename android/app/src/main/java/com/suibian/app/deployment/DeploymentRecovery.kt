package com.suibian.app.deployment

import com.suibian.app.data.db.ActionLogEntity
import com.suibian.app.executor.ActionType

internal val deploymentTypes = listOf(ActionType.CALENDAR_WRITE, ActionType.ALARM_SET)
internal val unresolvedCalendarWrites = setOf("confirmed", "calendar_review")

internal fun deploymentCanFinalize(rows: List<ActionLogEntity>): Boolean = deploymentTypes.all { type ->
    rows.filter { it.actionType == type.name }.maxByOrNull { it.id }?.decision == "done"
}

internal fun calendarUndoCandidate(rows: List<ActionLogEntity>): ActionLogEntity? = rows.asSequence()
    .filter { it.actionType == ActionType.CALENDAR_WRITE.name &&
        (it.decision in unresolvedCalendarWrites || it.decision == "done" || it.decision == "undo_pending") }
    .maxByOrNull { it.id }
    ?.takeIf { it.decision in setOf("done", "undo_pending") && it.undoData.isNotBlank() }
