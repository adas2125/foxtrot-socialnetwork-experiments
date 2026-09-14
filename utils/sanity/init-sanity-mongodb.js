// CLUSTER CHANGE when executed with mongosh: initialize post.post and its indexes.
// post_id_1 enforces unique post IDs; post_id_hashed supports sharding.
function check(ok, message) { if (!ok) throw Error(message || "MongoDB sanity check failed"); }
function worked(result) { check(result.ok === 1, JSON.stringify(result)); }
check(db.adminCommand({hello: 1}).msg === "isdbgrid");
const config = db.getSiblingDB("config");
const post = db.getSiblingDB("post");
const existing = config.collections.findOne({_id: "post.post", dropped: {$ne: true}});
if (existing && !existing.unsplittable) {
  check(JSON.stringify(existing.key) === JSON.stringify({post_id: "hashed"}));
}
worked(db.adminCommand({enableSharding: "post"}));
if (post.getCollectionInfos({name: "post"}).length === 0) {
  worked(post.createCollection("post"));
}
post.post.createIndex({post_id: 1}, {name: "post_id_1", unique: true});
post.post.createIndex({post_id: "hashed"}, {name: "post_id_hashed"});
if (!existing || existing.unsplittable) {
  worked(db.adminCommand({shardCollection: "post.post", key: {post_id: "hashed"}}));
}
const metadata = config.collections.findOne({_id: "post.post", dropped: {$ne: true}});
check(metadata && !metadata.unsplittable);
check(JSON.stringify(metadata.key) === JSON.stringify({post_id: "hashed"}));
const indexes = post.post.getIndexes();
check(indexes.some(i => i.name === "post_id_1" && i.unique && i.key.post_id === 1));
check(indexes.some(i => i.name === "post_id_hashed" && i.key.post_id === "hashed" && !i.unique));
printjson({namespace: metadata._id, key: metadata.key, indexes: indexes});
