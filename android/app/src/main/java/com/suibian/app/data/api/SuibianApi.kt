package com.suibian.app.data.api

import kotlinx.serialization.json.JsonObject
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

/**
 * 服务端接口 —— 技术文档第三节。P0 阶段请求/响应用 JsonObject（结构由 schema 目录下的
 * JSON Schema 文件约束）；W2 起逐接口替换成类型安全的数据类。
 * 注意：Kotlin 块注释可嵌套，注释里出现"斜杠星号"序列会吞掉后面的代码，
 * 写路径通配符时用文字描述（scripts 里配有静态扫描，CI 会拦）。
 * SSE 进度流用 okhttp-sse 单独封装（TODO(W4) data/api/SseClient.kt）。
 */
interface SuibianApi {
    @POST("v1/parse")
    suspend fun parse(@Body body: JsonObject): JsonObject

    @POST("v1/ocr/parse")
    suspend fun ocrParse(@Body body: JsonObject): JsonObject

    @POST("v1/generate")
    suspend fun generate(@Body body: JsonObject): JsonObject

    @GET("v1/generate/{jobId}")
    suspend fun generateResult(@Path("jobId") jobId: String): JsonObject

    @POST("v1/simulate")
    suspend fun simulate(@Body body: JsonObject): JsonObject

    @POST("v1/refresh")
    suspend fun refresh(@Body body: JsonObject): JsonObject

    @POST("v1/report")
    suspend fun report(@Body body: JsonObject): JsonObject

    @POST("v1/replan")
    suspend fun replan(@Body body: JsonObject): JsonObject

    @POST("v1/tick")
    suspend fun tick(@Body body: JsonObject): JsonObject

    @POST("v1/explain")
    suspend fun explain(@Body body: JsonObject): JsonObject

    @GET("v1/version")
    suspend fun version(): JsonObject

    @GET("v1/health")
    suspend fun health(): JsonObject

    @POST("v1/metrics")
    suspend fun metrics(@Body body: JsonObject): JsonObject
}
