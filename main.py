import itertools, time, urllib.parse
import PIL.Image, PIL.ImageDraw, PIL.ImageFont
import requests, tweepy
import env

auth = tweepy.OAuthHandler(env.CONSUMER_KEY, env.CONSUMER_SECRET)
auth.set_access_token(env.ACCESS_TOKEN, env.ACCESS_TOKEN_SECRET)
api = tweepy.API(auth)

self_name = api.me().screen_name

TRYAPL_ENDPOINT = "https://tryapl.org/Exec"
REPLY_TEMPLATE = "Run it online: {}\n\n{}"
RUN_ENDPOINT = "https://tryapl.org/?q={}&run"

def load_most_recent_processed():
    """Fetch the id of the most recently processed tweet upon bot start."""

    try:
        with open("most_recent_processed", "r") as f:
            mrp = f.read().strip()
    except FileNotFoundError:
        mrp = None
    return mrp

def save_most_recent_processed(mrp):
    """Ensure the id of the most recently processed tweet persists."""
    with open("most_recent_processed", "w") as f:
        f.write(str(mrp))

def parse_tweet(s):
    """Parse tweet for the code expressions to run."""

    in_code = in_string = False
    matches = []
    i = 0
    while i < len(s):
        char = s[i]
        if not in_code and char == "`":
            in_code = True
            match = ""
        elif in_code and not in_string:
            if char == "`":
                in_code = False
                matches.append(match)
            elif char == "\n":
                in_string = False
                matches.append(match)
                match = ""
            else:
                match += char
                in_string = char == "'"
        elif in_code and in_string:
            if char == "'" and i < len(s) - 1 and s[i+1] == "'":
                match += "''"
                i += 1
            else:
                in_string = char != "'"
                match += char
        i += 1
    return matches

def skip_tweet(tweet):
    """Check if this tweet should not be evaluated, to prevent recursion.

    This prevents users from evaluating expressions that evaluate to
    single-line results that would call the bot again, as that might
    create infinite recursion.
    Only exceptions are the bot's own original tweets.
    """

    return (
        tweet.user.screen_name == self_name and
        (rep_to := tweet.in_reply_to_screen_name) and
        rep_to != self_name
    )

# cf. https://github.com/twitter/twitter-text/tree/master/config
# to check what is the weight of each character in a Tweet.
SINGLE_CHAR_RANGES = [
    range(   0, 4351 + 1),
    range(8192, 8205 + 1),
    range(8208, 8223 + 1),
    range(8242, 8247 + 1),
]
def char_weight(char):
    """Returns weight value of a single character.

    Uses information from `twitter-text` to know the weight of each char.
    """
    if any(ord(char) in r for r in SINGLE_CHAR_RANGES):
        return 1
    else:
        return 2

def produce_code_result(result_lines):
    """Trims the code result to fit a tweet.

    Produces nothing for multi-line output and makes sure the line
    is short enough for single-line output.
    Also deals with the multi-weight of non-standard characters.
    """

    # If the result is single line, make sure the line is as long as possible.
    # If the result is multi-line, make sure lines don't get too long.
    if len(result_lines) != 1:
        return ""
    else:
        code_result = result_lines[0]
    weights = [char_weight(char) for char in code_result]
    acc = itertools.accumulate(weights)
    # According to https://help.twitter.com/en/using-twitter/how-to-tweet-a-link,
    # URLs take up 23 characters and subtract the length of the reply template.
    max_weight = 280 - 23 - len(REPLY_TEMPLATE)
    trimmed = ""
    for char, weight in zip(code_result, acc):
        if weight > max_weight:
            break
        trimmed += char
    if len(trimmed) < len(code_result):
        ellipsis = "..."
        trimmed = trimmed[:-len(ellipsis)] + ellipsis
    return trimmed

def generate_image(result_lines):
    """Generate an image with the code results.

    cf. https://stackoverflow.com/q/5414639/2828287.
    """

    if len(result_lines) > 100:
        result_lines = result_lines[:98] + ["∙∙∙"] + result_lines[99]
    result_lines = [
        line if len(line) <= 100 else line[:99] + "∙"
        for line in result_lines
    ]
    fontsize = 18
    px_per_char, px_per_line = 11, 22       # Figured these out through experimenting by hand.
    longest_line = max(len(line) for line in result_lines)
    img_width = max(longest_line*px_per_char, 400)
    img_height = max(len(result_lines)*px_per_line, 300)
    image = PIL.Image.new(
        "RGBA",
        (img_width, img_height),
        (255, 255, 255),
    )
    draw = PIL.ImageDraw.Draw(image)
    font = PIL.ImageFont.truetype("resources/Apl385.ttf", fontsize)
    draw.text((0, 0), "\n".join(result_lines), (0, 0, 0), font=font)
    return image

most_recent_processed = load_most_recent_processed()
called = time.time() - 12

while True:
    # I can request the mentions 75×/15 min, which gives around once every 12 seconds.
    # Sleep 12 seconds between requests, but discount any time I may have spent
    # processing previous tweets.
    time.sleep(
        max(0, min(12, 12 - (time.time() - called) + 0.01))
    )
    try:
        to_process = api.mentions_timeline(most_recent_processed, tweet_mode="extended")[::-1]
    except tweepy.error.RateLimitError:
        print("Skipping.")
        continue
    called = time.time()

    print(f"Processing {len(to_process)} tweet(s).")

    for tweet in to_process:
        if skip_tweet(tweet):
            print("Skipping potential infinite recursion.")
            most_recent_processed = tweet.id
            save_most_recent_processed(most_recent_processed)
            continue

        # Look for the code expressions to be ran.
        code_matches = parse_tweet(tweet.full_text)
        if not code_matches:
            api.update_status(
                "I see you mentioned me, but I found no code to evaluate.",
                in_reply_to_status_id=tweet.id,
                auto_populate_reply_metadata=True,
            )
            most_recent_processed = tweet.id
            save_most_recent_processed(most_recent_processed)
            continue

        # Build the mock interpreter session from the parsed code.
        session_lines = []
        result_lines = []
        ws_state, ws_id, ws_hash = "", 0, ""
        for match in code_matches:
            ws_state, ws_id, ws_hash, res = requests.post(
                TRYAPL_ENDPOINT,
                json=[ws_state, ws_id, ws_hash, match],
            ).json()
            session_lines.append(" "*6 + match)
            session_lines.extend(res)
            result_lines.extend(res)

        # Build the text reply.
        code = " ⋄ ".join(code_matches)
        tryapl_link = RUN_ENDPOINT.format(urllib.parse.quote_plus(code))
        code_result = produce_code_result(result_lines)
        reply = REPLY_TEMPLATE.format(tryapl_link, code_result).strip()

        # Build the image attachment.
        image = generate_image(session_lines)
        image.save("img.png")
        img_uploaded = api.media_upload("img.png")

        # Upload the reply.
        api.update_status(
            reply,
            in_reply_to_status_id=tweet.id,
            auto_populate_reply_metadata=True,
            media_ids=[img_uploaded.media_id_string],
        )
        most_recent_processed = tweet.id
        save_most_recent_processed(most_recent_processed)
