import time
import json
import redis
import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
import models
import asyncio
from statistics import mean, median, quantiles
import database

app = FastAPI()
load_dotenv()

TRIMET_APP_ID=os.getenv("TRIMET_APP_ID")
if not TRIMET_APP_ID:
    raise RuntimeError("TRIMET_APP_ID is not set!")

ITERATIONS = 100
client = httpx.AsyncClient()
redis_url = os.getenv("REDIS_URL")
redis_client = redis.from_url(redis_url)

API_URL = f"https://developer.trimet.org/ws/v2/arrivals?locIDs={14565}&showPosition=true&appID={TRIMET_APP_ID}&minutes=60"

#Using Stop 14565 for testing purposes
async def timeAPICall():
    t0 = time.perf_counter()
    response = await client.get(API_URL)
    response.raise_for_status()
    data = response.json()
    arrivals_db = {}

    arrivals_db = {}

    for arrival in data.get("resultSet", {}).get("arrival", []):
        status = arrival.get("status", "") 
        if status in ["estimated", "scheduled"]: #checks to make sure route will occur (not delayed or cancelled)
            eta = arrival.get("estimated") or arrival.get("scheduled")
            new_route = models.Route(
                stop_id=14565,
                route_id=arrival.get("route"),
                route_name=arrival.get("fullSign") or arrival.get("shortSign") or "",
                status=status,
                eta=eta,
                routeColor=arrival.get("routeColor", "")
            )
            arrivals_db[str(new_route.route_id) + ":" + str(eta)] = new_route.model_dump()

    return arrivals_db, time.perf_counter() - t0

async def timeCacheRetrieval():
    t0 = time.perf_counter()
    cached_data = redis_client.get("stop:14565:arrivals")
    data = cached_data.decode('utf-8')
    return json.loads(data), time.perf_counter() - t0

async def timeStopsAPICall():
    t0 = time.perf_counter()

    url = f"https://developer.trimet.org/ws/V1/stops?appID={TRIMET_APP_ID}&bbox=-122.836,45.387,-122.471,45.608&json=true"

    response = await client.get(url)
    response.raise_for_status()
    data  = response.json()
    stops_db = {}

    for stop in data.get("resultSet", {}).get("location", []):
        stop_id = stop.get("locid", -1)
        dir = stop.get("dir", "")
        new_station = models.Station(
            stop_id=stop_id,
            name=stop.get("desc", ""),
            dir=dir,
            lon=stop.get("lng", -1.0),
            lat=stop.get("lat", -1.0),
            dist=stop.get("metersDistance", 10000)
        )

        stops_db[str(stop_id) + ":" + dir] = new_station.model_dump()

    return stops_db, time.perf_counter() - t0

async def timeDBCalls():
    t0 = time.perf_counter()
    db = database.SessionLocal()
    try:
        toReturn = db.query(database.Stop).all()
        return toReturn, time.perf_counter() - t0
    finally:
        db.close()


def print_stats(label, timings):
    p90 = quantiles(timings, n=10)[-1]
    print(f"\n{label} (n={len(timings)})")
    print(f"  min   = {min(timings)*1000:6.2f} ms")
    print(f"  avg   = {mean(timings)*1000:6.2f} ms")
    print(f"  median= {median(timings)*1000:6.2f} ms")
    print(f"  90th pct= {p90*1000:6.2f} ms")
    print(f"  max   = {max(timings)*1000:6.2f} ms")

async def main():
    print("Warming up cache...")  
    data, api_time = await timeAPICall() #Remember to first sync stops in SwaggerUI
    stop_data, stop_api_time = await timeStopsAPICall()
    redis_client.set("stop:14565:arrivals", json.dumps(data))
    print(f"  (first API call took {api_time*1000:.2f} ms)\n")
    print(f"  (first Stop API call took {stop_api_time*1000:.2f} ms)\n")

    # 2) Measure API latency
    api_times = []
    for x in range(ITERATIONS):
        x, t = await timeAPICall()
        api_times.append(t)

    # 3) Measure cache latency
    cache_times = []
    for x in range(ITERATIONS):
        x, t = await timeCacheRetrieval()
        cache_times.append(t)

    # 4) Measure API latency for stops
    stops_api_times = []
    for x in range(ITERATIONS):
        x, t = await timeStopsAPICall()
        stops_api_times.append(t)

    # 5) Measure latency for GET from PostgreSQL
    db_times = []
    for x in range(ITERATIONS):
        x, t = await timeDBCalls()
        db_times.append(t)

    print_stats("API call latency", api_times)
    print_stats("Redis cache latency", cache_times)
    print_stats("Stops API call latency", stops_api_times)
    print_stats("Database retrieval latency", db_times)
    
    db = database.SessionLocal()
    print("Stop Database Entries: ", db.query(database.Stop).count())
    db.close()

if __name__ == "__main__":
    asyncio.run(main())










