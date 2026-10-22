# Meetup events page

One page that shows upcoming events from all your Meetup groups, refreshed automatically. Free to run, no branding.

- `index.html`: the page (list / grid / month views, group filters, event details popup)
- `groups.json`: your Meetup groups. **To add a 4th group, add one entry here.**
- `rules.json`: which group "owns" a co-hosted event (sets its tag, color and RSVP link). **Edit this to add more rules.**
- `sync.py`: downloads each group's public Meetup calendar feed, plus each event's photo from its own Meetup page, and writes `events.json`
- `.github/workflows/sync.yml`: runs `sync.py` every 3 hours on GitHub, free
- `events.json`: the data the page reads. It currently holds sample data and is replaced on the first sync.

## Setup (about 10 minutes)

1. Create a free account at github.com, then a **new public repository** (e.g. `stoic-events`).
2. Upload everything in this folder to it. Make sure the `.github/workflows/sync.yml` path is kept.
3. Repo **Settings → Pages**: set Source to "Deploy from a branch", branch `main`, folder `/ (root)`. Save.
4. Repo **Actions** tab: open "Sync Meetup events" and click **Run workflow** once. After about 30 seconds `events.json` is replaced with your real events.
5. Your page is now live at `https://<your-username>.github.io/<repo-name>/`.

From then on it refreshes itself every 3 hours.

## Adding it to thestoicgroup.com

Paste this into an "Embed / Custom HTML / Code" block on your site (use your real URL):

```html
<iframe id="events" src="https://YOUR-USERNAME.github.io/YOUR-REPO/?embed=1"
        style="width:100%;border:0;min-height:700px" title="Upcoming events"></iframe>
<script>
  window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'events-height')
      document.getElementById('events').style.height = e.data.height + 'px';
  });
</script>
```

Options you can add to the URL:
- `?embed=1`: hides the page heading so it blends into your site
- `&view=month` (or `grid`, `list`): the starting view
- `&group=stoicism` (comma-separated slugs): show only certain groups, handy for a page per group
- `&theme=dark` or `&theme=light`: force a theme (default follows the visitor's device)

## Customising

- **Colors / fonts:** the `:root` variables at the top of `index.html`. Group colors are in `groups.json`.
- **Title text:** the `<h1>` and `.sub` lines in `index.html`.
- **Refresh rate:** the `cron` line in `sync.yml` (`"17 */3 * * *"` means every 3 hours).

## Good to know

- Events that two of your groups co-host show up once. `rules.json` decides which group it belongs to: titles containing "stoicism 101" go to `stoicism`, "controlling your own emotional destiny" goes to `philosophy-therapy`. The other group's copy of the event is dropped, so it isn't shown, credited, or counted under that group's filter. Co-hosted events with no rule default to the first group listed in `groups.json`. To add a rule, copy a line in `rules.json` (`title_contains` is not case-sensitive).
- Event photos come from each event's own Meetup page. The sync fetches a page once per event and remembers the result, so it stays light. Photos are loaded straight from Meetup's servers, and an event with no photo (or a broken link) just shows without one.
- The feed itself has no photo or location, so the sync also marks events as "Online event" when their Meetup page says so. Physical venue addresses aren't captured.
- Only public groups work. If a group's feed can't be reached during a sync, the last known events for that group are kept.
- GitHub pauses scheduled jobs on repos with no activity for 60 days. Since the sync commits whenever events change, that rarely happens, but if updates stop, click Run workflow to wake it.
- If the GitHub run ever gets blocked by Meetup, you can run `python sync.py` on your own computer and upload the new `events.json`.
