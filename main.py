from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
import os, datetime, httpx, models, database, json
import redis

load_dotenv()
app = FastAPI()
TRIMET_APP_ID=os.getenv("TRIMET_API_KEY")
client = httpx.AsyncClient()
#database.Base.metadata.drop_all(bind=database.engine)
database.Base.metadata.create_all(bind=database.engine)
REDIS_URL=os.getenv("REDIS_URL")

redis_client = redis.from_url(REDIS_URL)

longitude = -122.6765
latitude = 45.5231

@app.get("/")
async def root():
    return {"message" : "Welcome to TriLive!"}

#returns arrivals follwing the route pyndantic models
@app.get("/arrivals")
async def get_arrivals(stop_id: int):
    url = f"https://developer.trimet.org/ws/v2/arrivals?locIDs={stop_id}&showPosition=true&appID={TRIMET_APP_ID}&showPosition=true&minutes=30"
    cache_key = f"stop:{stop_id}:arrivals"
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        data_json = cached_data.decode('utf-8')
        return json.loads(data_json)

    try:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    arrivals_db = {}

    for arrival in data.get("resultSet", {}).get("arrival", []):
        status = arrival.get("status", "") 
        if status in ["estimated", "scheduled"]: #checks to make sure route will occur (not delayed or cancelled)
            eta = arrival.get("estimated") or arrival.get("scheduled")
            converted_eta = timeConvert(eta) #converts from unix ms to HR:MIN AM or PM


            new_route = models.Route(
                stop_id=stop_id,
                route_id=arrival.get("route"),
                route_name=arrival.get("fullSign") or arrival.get("shortSign") or "",
                status=status,
                eta=converted_eta,
                routeColor=arrival.get("routeColor", "")
            )
            arrivals_db[str(new_route.route_id) + ":" + str(eta)] = new_route.model_dump()
    
    redis_client.setex(cache_key, 60, json.dumps(arrivals_db))    
    return arrivals_db # -> {k:v.dict() for k, v in arrivals_db.items()}

@app.get("/stops")
async def get_stop(longitude: float, latitude: float):
    radius = 3200 #radius of 3.2 km or roughly 2 miles
    url = f"https://developer.trimet.org/ws/V1/stops?appID={TRIMET_APP_ID}&ll={longitude},{latitude}&meters={radius}&json=true"

    try:
        response = await client.get(url)
        response.raise_for_status()
        data  = response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    stops_db = {}

    for stop in data.get("resultSet", {}).get("location", []):
        stop_id = stop.get("locid", -1)
        dir = stop.get("dir", "")
        new_station = models.Station(
            stop_id=stop_id,
            name=stop.get("desc", ""),
            dir=dir,
            long=stop.get("lng", -1.0),
            lat=stop.get("lat", -1.0),
            dist=stop.get("metersDistance", 10000)
        )

        stops_db[str(stop_id) + ":" + dir] = new_station.model_dump()

    
    return stops_db 

@app.post("/favorites")
async def post_favorites(stop_id: int, route_id: int, route_name: str):
    db = database.SessionLocal()
    fav_entry = db.query(database.Favorite).filter(database.Favorite.stop_id == stop_id, database.Favorite.route_id == route_id).first()
    if not fav_entry:
        new_fav = database.Favorite(stop_id=stop_id, route_id=route_id, route_name=route_name)
        db.add(new_fav)
        db.commit()
    db.close()

@app.get("/favorites")
async def get_favorites():
    db = database.SessionLocal()
    try:
        toReturn = db.query(database.Favorite).all()
        return toReturn
    finally:
        db.close()
    

def timeConvert(ms_timestamp: int):
    # Convert milliseconds to seconds for fromtimestamp()
    dt = datetime.datetime.fromtimestamp(ms_timestamp / 1000)
    # %-I is hour without leading zero (on Unix); %M is minutes; %p is AM/PM
    return dt.strftime("%-I:%M %p")
