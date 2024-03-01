import os
import json
import uuid
import time
import bcrypt
import random
import secrets
from pymongo import MongoClient
from sanic import Sanic, Websocket
from sanic.log import logger
from sanic.response import text
from sanic_ext import Extend

def dburl():
    """
        Simple utility function to get the MongoDB URL, so there are no errors when running locally or on the cloud.
    """
    try:
        return os.environ["DB_URL"]
    except KeyError:
        with open("db-url.txt") as f:
            return f.read()

def gen_motd():
    """
        Picks a new MOTD.
    """
    with open("motds.txt", "r") as f:
        return random.choice(f.readlines())

_motd = gen_motd()
motd_req_count = 0

app = Sanic("peer-server")
app.config.CORS_ORIGINS = "*"
Extend(app)

cluster = MongoClient(dburl())
db = cluster["test"]

apikeys = {}
open_feed_connections = {}

async def is_user_available(username, id):
    """
        Username should be a username AND a discriminator, not just a username.
        ID should be the user's ID.
    """
    for i in db["users"].find():
        if i["_id"] == "a":
            continue
        elif (f"{i['username']}#{i['discriminator']}" == username) or (i["_id"] == id):
            return False
        else:
            continue
    else:
        return True
    
async def is_chat_id_available(id):
    """
        Checks if a chat ID is available.
    """
    for i in db["chats"].find():
        if i["_id"] == "a":
            continue
        elif (i["_id"] == id):
            return False
        else:
            continue
    else:
        return True

@app.get("/")
async def index(req):
    return text("Hello world!")

@app.get("/motd")
async def motd(req):
    global motd_req_count, _motd
    motd_req_count += 1
    
    # Generate a new MOTD every ~~10~~ 5 requests.
    if motd_req_count > 4:
        _motd = gen_motd()
        motd_req_count = 0
    return text(_motd)

@app.post("/register_user")
async def register_user(req):
    """
        Creates a user account.
        Responds with an error message if something went wrong, or the user's new ID and full username (username + discriminator) if it was successfully created.
        Requires the user's username, discriminator (which should be generated on the frontend) and password in headers.
        An optional display name can also be included as a header.

        Request headers:
        username: the username of the user to be created.
        discriminator: the four-digit discriminator of the user to be created, generated randomly on the frontend.
        pswd: the password of the user to be created.
        (optional) display_name: the display name of the user to be created. [NOT IMPLEMENTED]
    """
    username = req.headers["username"]
    discriminator = req.headers["discriminator"]
    pswd = req.headers["pswd"]
    _id = uuid.uuid4().hex

    if "display_name" in req.headers.keys():
        display_name = req.headers["display_name"]
    else:
        display_name = None

    # Check for errors in the recieved data
    error = ""
    if (len(username) > 20) or (display_name != None and len(display_name) > 30):
        error = "TooLong"
    elif username == "":
        error = "UsernameRequired"
    
    if not await is_user_available(f"{username}#{discriminator}", _id):
        error = "UsernameTaken"

    allowed_chars = list("""ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz1234567890-_=+.>,<?!^&*()[]{}|~`%$:;""")
    for char in username:
        if char not in allowed_chars:
            error = "InvalidChars"

    # Return the JSON response for the request: either an error or a success
    if error != "":
        return text(json.dumps({"error": error}))
    else:
        db["users"].insert_one({"_id": str(_id), "username": str(username), "pswd": bcrypt.hashpw(pswd.encode("utf-8"), bcrypt.gensalt(12)), "discriminator": str(discriminator), "display_name": str(display_name)})
        return text(json.dumps({"error": "", "_id": str(_id), "user": f"{username}#{discriminator}"}))

@app.post("/create_chat")
async def create_chat(req):
    """
        Creates a groupchat.

        Request headers:
        chat_name: the name of the chat.
        chat_owner_apikey: the API key of the chat owner.
    """
    name = req.headers["chat_name"]
    owner = apikeys[req.headers["chat_owner_apikey"]]["_id"]

    # Create an ID for the chat and make sure it isn't already taken.
    _id = uuid.uuid4().hex
    while not is_chat_id_available(str(_id)):
        _id = uuid.uuid4().hex
    
    userdata = db["users"].find_one({"_id": owner})
    if "chats" not in userdata.keys():
        userdata["chats"] = []
    userdata["chats"].append(str(_id))

    db["users"].update_one({"_id": owner}, {"$set": {"chats": userdata["chats"]}})
    db["chats"].insert_one({"_id": str(_id), "name": name, "owner": owner, "members": [owner], "message_history": []})

    return text(json.dumps({"error": "", "id": str(_id)}))

@app.post("/add_to_chat")
async def add_to_chat(req):
    """
        Add a user to a groupchat.
        The inviter is the user creating the invite, whereas the invitee is the user receiving it.

        Request headers:
        inviter_apikey: the API key of the inviter.
        invitee: the user ID of the invitee.
        chat_id: the ID of the chat.
    """
    inviter = apikeys[req.headers["inviter_apikey"]]["_id"]
    invitee = req.headers["invitee"]
    chat = req.headers["chat_id"]

    chatdata = db["chats"].find_one({"_id": chat})
    print(inviter)
    print(chatdata)
    if (inviter not in chatdata["members"]) or (inviter != chatdata["owner"]):
        return text(json.dumps({"error": "NoInvitePerms", "inviter": inviter}))
    elif invitee in chatdata["members"]:
        return text(json.dumps({"error": "UserAlreadyInChat"}))
    else:
        userdata = db["users"].find_one({"_id": invitee})

        # Fix for adding nonexistant users: check against None and return an error if true.
        if userdata == None:
            return text(json.dumps({"error": "UserNotFound"}))

        # Users don't have a "chats" key by default.
        if "chats" not in userdata.keys():
            userdata["chats"] = []
        userdata["chats"].append(chat)
        chatdata["members"].append(invitee)

        db["users"].update_one({"_id": invitee}, {"$set": {"chats": userdata["chats"]}})
        db["chats"].update_one({"_id": chat}, {"$set": {"members": chatdata["members"]}})

        return text(json.dumps({"error": ""}))
    
@app.post("/remove_from_chat")
async def remove_from_chat(req):
    """
        Remove a user from a groupchat.
        The remover is the user responsible for removing the removee (the user being removed)

        Request headers:
        remover_apikey: the API key of the remover.
        removee: the ID of the removee.
        chat_id: the ID of the chat to remove the removee from.
    """
    remover = apikeys[req.headers["remover_apikey"]]
    removee = req.headers["removee"]
    chat = req.headers["chat_id"]

    chatdata = db["chats"].find_one({"_id": chat})
    userdata = db["users"].find_one({"_id": removee})
    if (chat not in userdata["chats"]) or (removee not in chatdata["members"]):
        return text(json.dumps({"error": "UserNotInChat"}))
    elif remover["_id"] != chatdata["owner"]:
        return text(json.dumps({"error": "NoRemovePerms"}))
    else:
        userdata["chats"].remove(chat)
        chatdata["members"].remove(removee)

        db["users"].update_one({"_id": removee}, {"$set": {"chats": userdata["chats"]}})
        db["chats"].update_one({"_id": chat}, {"$set": {"members": chatdata["members"]}})

        return text(json.dumps({"error": ""}))

@app.post("/auth")
async def auth(req):
    """
        Check if a user's password and username/discriminator match.

        Request headers:
        username: the username/discriminator combination, for example: "Bob#4391". This is split by the hashtag (#) into a username and discriminator in the code.
        pswd: the password to be checked.
    """
    username = req.headers["username"].split("#")[0]
    discriminator = req.headers["username"].split("#")[1]
    pswd = req.headers["pswd"]
    userdata = db["users"].find_one({"username": username, "discriminator": discriminator})

    pswd_check = bcrypt.checkpw(pswd.encode("utf8"), userdata["pswd"])
    try:
        if pswd_check:
            apikey = secrets.token_urlsafe(40)
            apikeys[apikey] = {"_id": userdata["_id"], "last_req": int(time.time())}
            return text(json.dumps({"success": True, "rec_username": username, "rec_discriminator": discriminator, "_id": userdata["_id"], "apikey": apikey, "pswd_check": pswd_check}))
        else:
            raise Exception()
    except Exception:
        return text(json.dumps({"success": False, "rec_username": username, "rec_discriminator": discriminator, "checkpw_result": pswd_check}))

@app.post("/msg")
async def msg(req):
    """
        The big important boy. Posts a message to a chat.

        Request headers:
        chat_id: the ID of the chat the message is to be created in.
        author_apikey: the API key of the message author.
        timestamp: the timestamp of the message being created, in seconds since the Unix epoch.
        content: the text content of the message.

        Logic flow:
        1. Check if the author is in the `members` array of the chat document.
            a. If false, return an error.
        2. Check if the length of `content` is longer than the message length limit (500)
            a. If true, return an error.
        2. Append the message data to the `message_history` array of the chat document.
            a. If no `message_history` array exists, create one first.
    """
    print(apikeys)
    print("---")
    print(open_feed_connections)
    chat = db["chats"].find_one({"_id": req.headers["chat_id"]})
    author = apikeys[req.headers["author_apikey"]]["_id"]
    timestamp = req.headers["timestamp"]
    content = req.headers["content"]

    if author not in chat["members"]:
        return text(json.dumps({"error": "UserNotInChat"}))
    elif len(content) > 500 or content.strip() == "":
        return text(json.dumps({"error": "IllegalMessageContent"}))
    else:
        if "message_history" not in chat.keys():
            chat["message_history"] = []
        chat["message_history"].append({
            "author": author,
            "content": content.strip(),
            "timestamp": timestamp
        })
        db["chats"].update_one({"_id": req.headers["chat_id"]}, {"$set": {"message_history": chat["message_history"]}})

        if chat["_id"] in open_feed_connections.keys():
            for client in open_feed_connections[chat["_id"]]:
                try:
                    await open_feed_connections[chat["_id"]][client].send(json.dumps({
                        "cmd": "livemsg",
                        "val": {
                            "author": author,
                            "username": db["users"].find_one({"_id": author})["username"],
                            "discriminator": str(db["users"].find_one({"_id": author})["discriminator"]),
                            "timestamp": timestamp,
                            "content": content
                        }
                    }))
                except Exception as e:
                    print(e)
        return text(json.dumps({"error": ""}))

@app.websocket("/chatfeed/<id>")
async def chatfeed(req, ws: Websocket, id):
    """
        WebSocket route for live message handling.
        Clients should post messages to the chat via the WebSocket connection when available, instead of using the /msg route.
        
        Logic flow:
        1. The server sends a {"cmd": "auth"} packet to the client upon connection.
        2. The client responds with its API key.
            a. If the API key is correct the client begins receiving live chat updates.
            b. If the API key is incorrect, return an error.
    """
    user = ""
    chatdata = db["chats"].find_one({"_id": id})
    await ws.send(json.dumps({"cmd": "auth"}))

    async for packet in ws:
        packet = json.loads(packet)
        print(f"Incoming packet: {packet}")

        match packet["cmd"]:
            case "auth":
                user = apikeys[packet["val"]["apikey"]]["_id"]

                if user not in chatdata["members"]:
                    await ws.send(json.dumps({"error": "UserNotInChat", "chat_members": chatdata["members"], "client_id": apikeys[packet["val"]["apikey"]]["_id"]}))
                    await ws.close()
                else:
                    if id not in open_feed_connections.keys():
                        open_feed_connections[id] = {}
                    open_feed_connections[id][user] = ws
                    await ws.send(json.dumps({"error": ""}))

@app.get("/user/<id>")
async def user(req, id):
    """
        Responds with publicly accesible data on the user, such as username, discriminator, etc.
        Requires the user's ID.
    """
    for user in db["users"].find():
        if user["_id"] == id:
            error = ""
            break
    else:
        error = "UserNotFound"

    if error != "":
        return text(json.dumps({"error": error}))
    else:
        userdata = db["users"].find_one({"_id": id})
        res = {"_id": userdata["_id"], "username": userdata["username"], "discriminator": userdata["discriminator"]}
        if "chats" in userdata.keys():
            res["chats"] = userdata["chats"]
        if "display_name" in userdata.keys():
            res["display_name"] = userdata["display_name"]
        return text(json.dumps(res))

@app.get("/chat/<id>")
async def chat(req, id):
    """
        Responds with publicly accesible data on the chat, such as name, members, etc.
        Requires the chat's ID.
    """
    for chat in db["chats"].find():
        if chat["_id"] == id:
            error = ""
            break
    else:
        error = "ChatNotFound"
    
    if error != "":
        return text(json.dumps({"error": error}))
    else:
        chatdata = db["chats"].find_one({"_id": id})
        res = {"error": "", "_id": chatdata["_id"], "name": chatdata["name"], "owner": chatdata["owner"], "members": [member for member in chatdata["members"]]}
        return text(json.dumps(res))

@app.get("/userid/<username>/<discriminator>")
async def userid(req, username, discriminator):
    """
        Responds with the ID of the username and discriminator provided.
    """
    for userdata in db["users"].find():
        if userdata["_id"] == "a":
            continue
        if f"{username}#{discriminator}" == f"{userdata['username']}#{userdata['discriminator']}":
            _id = userdata["_id"]
            break
    else:
        return text(json.dumps({"error": "UserNotFound"}))
    
    return text(json.dumps({"error": "", "userid": _id}))

@app.post("/chatfetch/<id>")
async def chatfetch(req, id):
    """
        Fetch the latest messages from the given chat.
        Requires the chat's ID.

        Request headers:
        apikey: The API key of the user fetching the messages.
        limit: Optional, the amount of messages to fetch. Defaults to 50.
    """
    if "limit" in req.headers:
        limit = req.headers["limit"]
    else:
        limit = 50

    for chat in db["chats"].find():
        if chat["_id"] == id:
            error = ""
            break
    else:
        error = "ChatNotFound"

    if error != "":
        return text(json.dumps({"error": error}))
    else:
        chatdata = db["chats"].find_one({"_id": id})
        """
        res_messages = []

        for message in chatdata["message_history"][:limit]:
            res_messages.append({
                "author": message["author"],
                "username": db["users"].find_one({"_id": message["author"]})["username"],
                "discriminator": str(db["users"].find_one({"_id": message["author"]})["discriminator"]),
                "timestamp": message["timestamp"],
                "content": message["content"]
            })
        print(res_messages)
        """

        res = {"error": "", "messages": chatdata["message_history"][:limit]}
        return text(json.dumps(res))

@app.get("/allusers")
async def allusers(req):
    """
        Responds with a count of how many users are registered and an array of all user ids.
    """
    res = []
    for user in db["users"].find():
        if user["_id"] == "a":
            continue
        res.append(user["_id"])
    return text(json.dumps({"user_count": len(res), "users": res}))

@app.get("/allchats")
async def allchats(req):
    """
        Responds with a count of how many chats exist and an array of all chat ids.
    """
    res = []
    for chat in db["chats"].find():
        if chat["_id"] == "a":
            continue
        res.append(chat["_id"])
    return text(json.dumps({"chat_count": len(res), "chats": res}))

# Start the server
if __name__ == "__main__":
    app.run("0.0.0.0")