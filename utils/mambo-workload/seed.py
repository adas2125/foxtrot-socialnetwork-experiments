"""CLUSTER CHANGE: insert a fresh synthetic HomeTimeline dataset."""
import argparse
import json
import random
import string
import sys
import time

from bson.int64 import Int64
from pymongo import MongoClient
from redis.cluster import RedisCluster


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=int, required=True)
    parser.add_argument("--readers", type=int, required=True)
    parser.add_argument("--posts", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    assert 0 < args.readers <= 100000 and 10 <= args.posts <= 2048
    assert 0 < args.base and args.base + args.readers < 2**53
    total = args.readers * args.posts
    first_post = args.base * 10000000
    assert total < 10000000 and first_post + total < 2**63
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise RuntimeError("Pass the MongoDB password on stdin")
    mongo = MongoClient(
        "sn-mongodb-sharded.mambo-socialnetwork.svc.cluster.local", 27017,
        username="root", password=password, authSource="admin",
        serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
        socketTimeoutMS=60000,
    )
    if mongo.admin.command("hello").get("msg") != "isdbgrid":
        raise RuntimeError("Expected mongos")
    redis = RedisCluster(
        host="redis-cluster-headless.foxtrot.svc.cluster.local", port=6379,
        decode_responses=True, socket_timeout=30, socket_connect_timeout=10,
    )
    posts = mongo.post.post
    selector = {"post_id": {"$gte": Int64(first_post), "$lt": Int64(first_post + total)}}
    if posts.find_one(selector, {"_id": 1}) is not None:
        raise RuntimeError("Post range exists; inspect the previous load")
    for reader in range(args.base, args.base + args.readers):
        if redis.exists(str(reader)):
            raise RuntimeError(f"Reader key exists: {reader}")

    # Atomic creation also refuses a key appearing after preflight.
    create_timeline = """
    if redis.call('EXISTS', KEYS[1]) ~= 0 then
      return redis.error_reply('Reader key already exists')
    end
    return redis.call('ZADD', KEYS[1], unpack(ARGV))
    """
    rng = random.Random(args.seed)
    alphabet = string.ascii_letters + string.digits
    timestamp = int(time.time() * 1000)
    for offset in range(args.readers):
        reader = args.base + offset
        batch, timeline = [], []
        for index in range(args.posts):
            post_id = first_post + offset * args.posts + index
            prefix = f"mambo-{args.base}-{post_id}:"
            text = prefix + "".join(rng.choices(alphabet, k=1024 - len(prefix)))
            batch.append({
                "post_id": Int64(post_id), "req_id": Int64(post_id),
                "timestamp": Int64(timestamp + index), "post_type": 0,
                "text": text,
                "creator": {"user_id": Int64(reader), "username": f"mambo_{reader}"},
                "urls": [], "user_mentions": [], "media": [],
            })
            timeline.extend((index, str(post_id)))
        posts.insert_many(batch, ordered=True)
        redis.eval(create_timeline, 1, str(reader), *timeline)
        if redis.zcard(str(reader)) != args.posts:
            raise RuntimeError(f"Wrong timeline size: {reader}")
        newest = str(first_post + (offset + 1) * args.posts - 1)
        if redis.zrevrange(str(reader), 0, 0) != [newest]:
            raise RuntimeError(f"Wrong newest post: {reader}")
        print(json.dumps({"readers_completed": offset + 1, "last_reader": reader}), flush=True)
    count = posts.count_documents(selector)
    if count != total:
        raise RuntimeError(f"Expected {total} posts, found {count}")
    print(json.dumps({"verified_posts": count, "base": args.base, "first_post": first_post}))


if __name__ == "__main__":
    main()
