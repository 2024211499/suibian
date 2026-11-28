package com.suibian.app.ui.home

import com.suibian.app.data.db.PlanEntity
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.put

@ConsistentCopyVisibility
data class HomePlan private constructor(val graph: JsonObject, val summary: PlanSummary) {
    fun toEntity(updatedAt: Long): PlanEntity = PlanEntity(
        planId = summary.id,
        version = summary.version,
        status = summary.status,
        json = graph.toString(),
        updatedAt = updatedAt,
        isCurrent = true,
    )

    fun inspectionRequest(departMin: Int): JsonObject = buildJsonObject {
        put("plan", graph)
        put("depart_min", departMin)
    }

    fun rainRequest(departMin: Int, delayMin: Int): JsonObject? {
        val origin = summary.nodes.firstOrNull { it.kind == "origin" }
        val leg = summary.edges.firstOrNull { it.from == origin?.id } ?: summary.edges.firstOrNull()
        return leg?.let {
            buildJsonObject {
                inspectionRequest(departMin).forEach { (key, value) -> put(key, value) }
                put("trigger", buildJsonObject {
                    put("kind", "route_delay")
                    put("data", buildJsonObject {
                        put("edge_id", leg.id)
                        put("delay_min", delayMin)
                    })
                })
            }
        }
    }

    companion object {
        fun from(graph: JsonObject, json: Json): HomePlan = HomePlan(graph, json.decodeFromJsonElement(graph))
    }
}

@Serializable
data class PlanSummary(
    @SerialName("plan_id") val id: String,
    val version: Int,
    val status: String,
    val title: String,
    val date: String,
    val nodes: List<PlanStop>,
    val edges: List<PlanLeg>,
    @SerialName("p_target") val targetProbability: Double? = null,
)

@Serializable
data class PlanStop(val id: String, val name: String, val kind: String? = null, val window: StopWindow? = null)

@Serializable
data class PlanLeg(val id: String, val from: String)

@Serializable
data class StopWindow(val earliest: String? = null, val latest: String? = null)

@Serializable
data class PlanInspection(
    @SerialName("p_all") val probability: Double,
    @SerialName("depart_by") val departBy: String? = null,
    val weakest: WeakestStop? = null,
    @SerialName("p_target") val targetProbability: Double? = null,
) {
    companion object {
        fun from(response: JsonObject, json: Json): PlanInspection = json.decodeFromJsonElement(
            requireNotNull(response["sim"]) { "体检响应缺少 sim" },
        )
    }
}

@Serializable
data class WeakestStop(val node: String)

@Serializable
data class RainComparison(
    @SerialName("base_version") val baseVersion: Int,
    val before: PlanInspection,
    val after: PlanInspection,
    val affected: List<RainImpact>,
    val options: List<RainOption>,
)

@Serializable
data class RainImpact(val node: String, val before: NodePrediction, val after: NodePrediction)

@Serializable
data class NodePrediction(@SerialName("arrive_p90") val arrivalP90: Double)

@Serializable
data class RainOption(@SerialName("option_id") val id: String, val sim: PlanInspection)

@Serializable
internal data class ParseResponse(
    @SerialName("draft_plan") val draft: JsonObject? = null,
    val questions: List<String> = emptyList(),
)

internal fun parseDepartureMinute(text: String): Int? {
    val (hourText, minuteText) = departurePattern.matchEntire(text.trim())?.destructured ?: return null
    val hour = hourText.toInt()
    val minute = minuteText.toInt()
    return if (hour in 0..23 && minute in 0..59) hour * 60 + minute else null
}

private val departurePattern = Regex("([0-9]{1,2}):([0-9]{2})")
