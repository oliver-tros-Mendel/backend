const BASE = "";
const API = "/api";
const MAX_LENGTH = 280;
const MAX_IMAGE_MB = 5;

const authBox = document.getElementById("auth");
const authUsername = document.getElementById("authUsername");
const authPassword = document.getElementById("authPassword");
const authError = document.getElementById("authError");
const loginBtn = document.getElementById("loginBtn");
const registerBtn = document.getElementById("registerBtn");

const userBar = document.getElementById("userBar");
const currentUserEl = document.getElementById("currentUser");
const logoutBtn = document.getElementById("logoutBtn");

const composer = document.getElementById("composer");
const contentInput = document.getElementById("content");
const counter = document.getElementById("counter");
const postBtn = document.getElementById("postBtn");
const feed = document.getElementById("feed");
const feedTitle = document.getElementById("feedTitle");
const profileHeader = document.getElementById("profileHeader");
const profileStats = document.getElementById("profileStats");
const followBtn = document.getElementById("followBtn");
const tabs = document.getElementById("tabs");
const tabAll = document.getElementById("tabAll");
const tabFollowing = document.getElementById("tabFollowing");

const imageInput = document.getElementById("imageInput");
const preview = document.getElementById("preview");
const previewImg = document.getElementById("previewImg");
const removeImageBtn = document.getElementById("removeImageBtn");

let token = localStorage.getItem("token");
let currentUser = localStorage.getItem("username");

// Which feed is shown on the home page: "all" or "following"
let feedMode = "all";

// Remembers which posts have their comments open,
// so they stay open when the feed reloads
const openComments = new Set();

// ---------- profile routing ----------

// "#user/alice" in the address bar means: show only alice's posts
function getProfileFromHash() {
  const hash = location.hash;
  if (hash.startsWith("#user/")) {
    return decodeURIComponent(hash.slice("#user/".length));
  }
  return null;
}

let profileUser = getProfileFromHash();

// A clickable username that links to that user's profile
function userLink(username) {
  const a = document.createElement("a");
  a.className = "user-link";
  a.href = "#user/" + encodeURIComponent(username);
  a.textContent = username;
  return a;
}

window.addEventListener("hashchange", function () {
  profileUser = getProfileFromHash();
  window.scrollTo(0, 0);
  loadPosts();
});

// ---------- helpers ----------

async function api(path, options = {}) {
  const headers = {};
  // For file uploads (FormData) the browser must set Content-Type itself
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (token) headers["Authorization"] = "Bearer " + token;

  const response = await fetch(API + path, { ...options, headers: headers });

  // Token no longer valid on the server: log out locally
  if (response.status === 401 && token) {
    clearLogin();
    loadPosts();
  }
  return response;
}

function saveLogin(data) {
  token = data.token;
  currentUser = data.username;
  localStorage.setItem("token", token);
  localStorage.setItem("username", currentUser);
}

function clearLogin() {
  token = null;
  currentUser = null;
  feedMode = "all";
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  updateUI();
}

function updateUI() {
  const loggedIn = token !== null;
  authBox.classList.toggle("hidden", loggedIn);
  userBar.classList.toggle("hidden", !loggedIn);
  composer.classList.toggle("hidden", !loggedIn);
  if (loggedIn) currentUserEl.textContent = currentUser;
}

// ---------- login / register / logout ----------

async function authenticate(endpoint) {
  authError.textContent = "";

  const response = await api(endpoint, {
    method: "POST",
    body: JSON.stringify({
      username: authUsername.value,
      password: authPassword.value,
    }),
  });
  const data = await response.json();

  if (!response.ok) {
    authError.textContent = data.error;
    return;
  }

  saveLogin(data);
  authUsername.value = "";
  authPassword.value = "";
  updateUI();
  loadPosts();
}

loginBtn.addEventListener("click", function () {
  authenticate("/login");
});

registerBtn.addEventListener("click", function () {
  authenticate("/register");
});

logoutBtn.addEventListener("click", async function () {
  await api("/logout", { method: "POST" });
  clearLogin();
  loadPosts();
});

// Pressing Enter in either login field logs in
function loginOnEnter(event) {
  if (event.key === "Enter") {
    authenticate("/login");
  }
}
authUsername.addEventListener("keydown", loginOnEnter);
authPassword.addEventListener("keydown", loginOnEnter);

// ---------- character counter ----------

function updateCounter() {
  const remaining = MAX_LENGTH - contentInput.value.length;
  counter.textContent = remaining;
  counter.classList.toggle("warning", remaining <= 20);
}

contentInput.addEventListener("input", updateCounter);

// Ctrl+Enter (or Cmd+Enter on Mac) posts
contentInput.addEventListener("keydown", function (event) {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    postBtn.click();
  }
});

// ---------- picking a picture ----------

function clearImage() {
  imageInput.value = "";
  previewImg.removeAttribute("src");
  preview.classList.add("hidden");
}

imageInput.addEventListener("change", function () {
  const file = imageInput.files[0];
  if (!file) return;

  if (file.size > MAX_IMAGE_MB * 1024 * 1024) {
    alert("Image is too large (max " + MAX_IMAGE_MB + " MB)");
    clearImage();
    return;
  }

  previewImg.src = URL.createObjectURL(file);
  preview.classList.remove("hidden");
});

removeImageBtn.addEventListener("click", clearImage);

// ---------- time ----------

function timeAgo(isoString) {
  const seconds = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);

  if (seconds < 60) return "just now";

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + (minutes === 1 ? " minute ago" : " minutes ago");

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + (hours === 1 ? " hour ago" : " hours ago");

  const days = Math.floor(hours / 24);
  if (days < 7) return days + (days === 1 ? " day ago" : " days ago");

  return new Date(isoString).toLocaleDateString();
}

// ---------- comments ----------

function renderComment(comment) {
  const el = document.createElement("div");
  el.className = "comment";

  const text = document.createElement("p");
  const b = document.createElement("b");
  b.append(userLink(comment.username), ":");
  text.append(b, " " + comment.content);

  const time = document.createElement("small");
  time.className = "time";
  time.textContent = timeAgo(comment.created_at);

  el.append(text, time);

  if (comment.mine) {
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "comment-delete";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async function () {
      if (!confirm("Delete this comment?")) return;
      await api("/comments/" + comment.id, { method: "DELETE" });
      loadPosts();
    });
    el.append(deleteBtn);
  }

  return el;
}

async function loadComments(postId, box) {
  const response = await api("/posts/" + postId + "/comments");
  const comments = await response.json();

  box.innerHTML = "";

  if (comments.length === 0) {
    const empty = document.createElement("p");
    empty.className = "time";
    empty.textContent = "No comments yet.";
    box.append(empty);
  }

  for (const comment of comments) {
    box.append(renderComment(comment));
  }

  // Only logged-in users get a reply box
  if (token) {
    const form = document.createElement("div");
    form.className = "comment-form";

    const input = document.createElement("input");
    input.className = "comment-input";
    input.placeholder = "Write a reply...";
    input.maxLength = 200;

    const sendBtn = document.createElement("button");
    sendBtn.textContent = "Reply";

    async function send() {
      if (input.value.trim() === "") return;

      const result = await api("/posts/" + postId + "/comments", {
        method: "POST",
        body: JSON.stringify({ content: input.value }),
      });

      if (!result.ok) {
        const data = await result.json();
        alert(data.error);
        return;
      }
      loadPosts();
    }

    sendBtn.addEventListener("click", send);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter") send();
    });

    form.append(input, sendBtn);
    box.append(form);
  }
}

// ---------- posts ----------

function renderPost(post) {
  const el = document.createElement("div");
  el.className = "post";

  const p = document.createElement("p");
  const b = document.createElement("b");
  b.append(userLink(post.username), ":");
  p.append(b, " " + post.content);

  const time = document.createElement("small");
  time.className = "time";
  time.textContent = timeAgo(post.created_at);
  time.title = new Date(post.created_at).toLocaleString();

  const actions = document.createElement("div");
  actions.className = "actions";

  const likeBtn = document.createElement("button");
  likeBtn.className = "like-btn" + (post.liked_by_me ? " liked" : "");
  likeBtn.textContent = "♥ " + post.likes;
  likeBtn.addEventListener("click", async function () {
    if (!token) {
      alert("Log in to like posts");
      return;
    }
    await api("/posts/" + post.id + "/like", { method: "POST" });
    loadPosts();
  });
  actions.append(likeBtn);

  // The box where this post's comments appear
  const commentsBox = document.createElement("div");
  commentsBox.className = "comments hidden";

  const commentBtn = document.createElement("button");
  commentBtn.className = "comment-btn";
  commentBtn.textContent = "💬 " + post.comments;
  commentBtn.addEventListener("click", function () {
    if (openComments.has(post.id)) {
      openComments.delete(post.id);
      commentsBox.classList.add("hidden");
    } else {
      openComments.add(post.id);
      commentsBox.classList.remove("hidden");
      loadComments(post.id, commentsBox);
    }
  });
  actions.append(commentBtn);

  if (post.mine) {
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "delete-btn";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async function () {
      if (!confirm("Delete this post?")) return;
      openComments.delete(post.id);
      await api("/posts/" + post.id, { method: "DELETE" });
      loadPosts();
    });
    actions.append(deleteBtn);
  }

  el.append(p);

  if (post.image) {
    const img = document.createElement("img");
    img.className = "post-image";
    img.src = BASE + post.image;
    img.alt = "Picture posted by " + post.username;
    img.title = "Click to open full size";
    img.addEventListener("click", function () {
      window.open(img.src, "_blank");
    });
    el.append(img);
  }

  el.append(time, actions, commentsBox);

  // If this post's comments were open before the reload, open them again
  if (openComments.has(post.id)) {
    commentsBox.classList.remove("hidden");
    loadComments(post.id, commentsBox);
  }

  return el;
}

// Fills in the profile header: stats and the follow button
async function loadProfileInfo(posts) {
  followBtn.classList.add("hidden");

  const response = await api("/users/" + encodeURIComponent(profileUser));
  if (!response.ok) {
    profileStats.textContent = "User not found.";
    return;
  }
  const info = await response.json();

  let totalLikes = 0;
  for (const post of posts) totalLikes += post.likes;

  profileStats.textContent =
    posts.length + (posts.length === 1 ? " post" : " posts") +
    " · " + totalLikes + (totalLikes === 1 ? " like" : " likes") + " received" +
    " · " + info.followers + (info.followers === 1 ? " follower" : " followers") +
    " · " + info.following + " following";

  // Show the button only when logged in, and never on your own profile
  if (token && !info.is_me) {
    followBtn.classList.remove("hidden");
    followBtn.textContent = info.followed_by_me ? "Unfollow" : "Follow";
    followBtn.classList.toggle("following", info.followed_by_me);
  }
}

async function loadPosts() {
  // The tabs only make sense when logged in and on the home page
  tabs.classList.toggle("hidden", !token || profileUser !== null);
  tabAll.classList.toggle("active", feedMode === "all");
  tabFollowing.classList.toggle("active", feedMode === "following");

  const followingFeed = !profileUser && feedMode === "following" && token;

  // Ask the server for the right set of posts
  let path = "/posts";
  if (profileUser) {
    path = "/posts?user=" + encodeURIComponent(profileUser);
  } else if (followingFeed) {
    path = "/posts?feed=following";
  }

  const response = await api(path);
  if (!response.ok) return;
  const posts = await response.json();

  // Heading and profile header depend on which page we're on
  if (profileUser) {
    feedTitle.textContent = "Posts by " + profileUser;
    await loadProfileInfo(posts);
    profileHeader.classList.remove("hidden");
  } else {
    feedTitle.textContent = followingFeed ? "Following" : "Feed";
    profileHeader.classList.add("hidden");
  }

  feed.innerHTML = "";

  if (posts.length === 0) {
    const empty = document.createElement("p");
    empty.className = "time";
    if (profileUser) {
      empty.textContent = "This user has no posts yet.";
    } else if (followingFeed) {
      empty.textContent = "Nothing here yet. Follow some people to see their posts.";
    } else {
      empty.textContent = "No posts yet.";
    }
    feed.append(empty);
  }

  for (const post of posts) {
    feed.prepend(renderPost(post));
  }
}

// ---------- tabs and follow button ----------

tabAll.addEventListener("click", function () {
  feedMode = "all";
  loadPosts();
});

tabFollowing.addEventListener("click", function () {
  feedMode = "following";
  loadPosts();
});

followBtn.addEventListener("click", async function () {
  await api("/users/" + encodeURIComponent(profileUser) + "/follow", {
    method: "POST",
  });
  loadPosts();
});

// ---------- creating a post ----------

postBtn.addEventListener("click", async function () {
  const content = contentInput.value;
  const file = imageInput.files[0];

  if (content.trim() === "" && !file) {
    alert("Write something or add a picture first!");
    return;
  }

  // FormData can carry text and a file together
  const formData = new FormData();
  formData.append("content", content);
  if (file) formData.append("image", file);

  const response = await api("/posts", { method: "POST", body: formData });

  if (!response.ok) {
    const data = await response.json();
    alert(data.error);
    return;
  }

  contentInput.value = "";
  clearImage();
  updateCounter();
  loadPosts();
});

// ---------- start ----------

updateUI();
updateCounter();
loadPosts();

// Refresh the feed every 30 seconds so times stay accurate
// and new posts from other people appear.
// Skip the refresh while someone is typing a reply, so it isn't wiped.
setInterval(function () {
  const active = document.activeElement;
  if (active && active.classList.contains("comment-input")) return;
  loadPosts();
}, 30000);

// ---------- dark mode ----------

const themeBtn = document.getElementById("themeBtn");

function showThemeIcon() {
  const theme = document.documentElement.getAttribute("data-theme");
  // The button shows what you will switch TO
  themeBtn.textContent = theme === "dark" ? "☀️" : "🌙";
}

themeBtn.addEventListener("click", function () {
  const current = document.documentElement.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";

  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme, next");
  showThemeIcon();
});

showThemeIcon();
