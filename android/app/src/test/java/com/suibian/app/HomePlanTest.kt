package com.suibian.app

import com.suibian.app.ui.home.HomePlan
import com.suibian.app.ui.home.PlanInspection
import com.suibian.app.ui.home.RainComparison
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HomePlanTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun `plan retains complete graph and server version when saved and inspected`() {
        val graph = objectFrom(
            """{
                "plan_id":"p_demo01","version":3,"status":"DRAFT","title":"明早赶课","date":"2026-12-05",
                "nodes":[{"id":"n0","name":"宿舍"},{"id":"n1","name":"三教","window":{"latest":"08:00"}}],
                "edges":[{"id":"e1","from":"n0","to":"n1","eta_min":15}],"constraints":[]
            }""",
        )
        val plan = HomePlan.from(graph, json)
        val saved = plan.toEntity(updatedAt = 10)
        assertEquals("p_demo01", saved.planId)
        assertEquals(3, saved.version)
        assertEquals("08:00", plan.summary.nodes.last().window?.latest)
        val reopened = HomePlan.from(objectFrom(saved.json), json)
        assertEquals(graph, reopened.inspectionRequest(420)["plan"])
        assertEquals("420", reopened.inspectionRequest(420)["depart_min"].toString())
        val rainRequest = requireNotNull(reopened.rainRequest(420, 15))
        assertEquals(graph, rainRequest["plan"])
        assertEquals("420", rainRequest["depart_min"].toString())
        assertEquals(
            objectFrom("""{"kind":"route_delay","data":{"edge_id":"e1","delay_min":15}}"""),
            rainRequest["trigger"],
        )
    }

    @Test
    fun `zero probability remains distinct from unavailable latest departure`() {
        val inspection = PlanInspection.from(
            objectFrom("""{"sim":{"p_all":0.0,"depart_by":null},"evidence":{}}"""),
            json,
        )
        assertEquals(0.0, inspection.probability, 0.0)
        assertNull(inspection.departBy)
    }

    @Test
    fun `rain comparison retains arrival impact when no acceptable option exists`() {
        val response = objectFrom(
            """{
                "base_version":1,"before":{"p_all":1.0},"after":{"p_all":0.4},
                "affected":[{"node":"n1","before":{"arrive_p90":438.5},"after":{"arrive_p90":461.2}}],
                "options":[]
            }""",
        )
        val comparison = json.decodeFromJsonElement<RainComparison>(response)
        assertEquals(1, comparison.baseVersion)
        assertEquals(0.4, comparison.after.probability, 0.0)
        val impact = comparison.affected.single()
        assertEquals("n1", impact.node)
        assertEquals(438.5, impact.before.arrivalP90, 0.0)
        assertEquals(461.2, impact.after.arrivalP90, 0.0)
        assertEquals(emptyList<Any>(), comparison.options)
    }

    private fun objectFrom(value: String): JsonObject = json.parseToJsonElement(value).jsonObject
}
