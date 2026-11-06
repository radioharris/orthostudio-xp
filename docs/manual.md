# OrthoStudio XP manual: clouds and settings profiles

What came with 1.4.0, step by step, with where to click. The rest of the app is described in the
[README](../README.md) and in [how it works](how-it-works.md).

- [Replacing the clouds of a tile](#replacing-the-clouds-of-a-tile)
- [Settings profiles](#settings-profiles)

## Replacing the clouds of a tile

Aerial photos are sometimes taken under a few clouds, and a tile built from them shows the clouds
on the ground in X-Plane. OrthoStudio XP finds them and puts in their place the ground of another
source that was clear there: Esri, EOX, or a national source such as PDOK in the Netherlands. You
choose which clouds go and which source takes each one; the tile keeps your choice in every build
after. What the map shows is what X-Plane shows.

### 1. Open the tile's clouds

In the **Library**, on the line of a tile built by OrthoStudio XP, press **Clouds…**. The Plan's
map comes over the tile, drawn as X-Plane draws it, from the tile's own files: nothing is
downloaded. A tile built by an earlier version is built once again with this one first: the panel
says so.

### 2. Look for the clouds

Press **Find clouds**. The first time, the app asks to download the tool that finds the clouds:
about 110 MB, OpenCV included, depending on the system. It is downloaded once and runs on your
computer: nothing is sent anywhere.

The search reads the tile, a second time in more detail where the tile is sharp enough to hold
small clouds, then tries every other source covering the tile under each cloud: whether it has a
picture there, whether it is clear, and how well its ground fits the photo around the cloud. On a
ZL14 tile it takes about a minute. The panel shows where it is; **Stop** ends it.

### 3. Choose what to replace

Each cloud found is outlined in yellow on the map and listed in the panel with its size and its
sources, for instance *Esri World Imagery · clear · fit 93 %*, the best first. A source showing
the same photo as the tile, which would bring the cloud back, or with no picture there, is greyed.

- Every cloud comes **unticked**. The tool can take something pale for a cloud, such as light,
  muddy water near a coast: look before you tick. **Show** brings the map to a cloud.
- **Tick** a cloud, or click its outline on the map: its source's images are fetched at the
  tile's own sharpness, in a few seconds, and its line says *Replaced by Esri World Imagery after
  Apply*. Its outline turns to the accent colour.
- To take another source, choose it in the cloud's list: it is ticked with it.
- A source that did not answer or refused for now says so in the line: **Get its images** asks
  it again.

### 4. Before and After

The banner over the map has two views:

- **After**: what X-Plane shows, the ticked clouds replaced, or what it will show once you press
  Apply.
- **Before**: the tile with no cloud replaced.

### 5. A cloud the search missed: outline it

Press **Outline a cloud** in the banner, then click around the cloud on the map, point by point.
Double-click the last point, or press Enter, to close it; Escape gives up. The outline takes the
place of the clouds found under it, is tried under the sources at once and comes in the list
ticked with the best, named *Outline 1*, with a bin to take it out.

### 6. Apply

Nothing changes in X-Plane until you press **Apply (3 changes)**. The app says what will change,
and what waits (a cloud whose images are not in yet stays out). The tile is built again with the
settings it was built with, whatever profile is in use now: only its clouds change, and only the
textures under them are made again, usually in seconds. Works shows the build. If you stop it, or
it fails, the tile keeps the clouds it had, and you can press **Apply** again.

**Discard changes** puts the list back to what the tile holds.

### 7. Afterwards

The tile keeps its clouds replaced: every build of it after, from the Plan, the Library or Works,
makes them again on the same photo. The Library says how many it has replaced and how many wait.

A cloud is chosen on a photo. Build the tile from another source or at another level, change the
colours of the tile or of a zone near the cloud, or let its source send new images, and the photo
under the cloud is another one. The build then puts the photo back there, as photographed, and
the cloud's line says why. It stays ticked: get its images again on the new photo, and one
**Apply** replaces it again.

**Restore the original photos** asks, then builds the tile again with none of its clouds
replaced; your list stays, to tick them again later. Deleting a tile forgets its clouds.

While the command line builds a tile, the app does not build that tile too: it says so and you
build it again once the first build is over.

## Settings profiles

A profile is a set of settings kept under a name, to build some tiles one way and others another:
sharp, with every airfield, for the mountains you fly low over; light for the plains you cross.

### 1. Make a profile

In **Settings**, change the answers you want, or start from a preset. Then press **Save as a new
profile…** beside **Save**. The bar asks for a name: type it and press **Create the profile**.

![The bar asking the new profile's name, "Everyday", with Create the profile and Cancel](images/manual/profile-new.png)

The settings saved until then stay in a profile of their own, called *Main* the first time.

### 2. The profile in use

The top of Settings shows the profile in use. Choose another in the list to work on it; **Rename**
and **Delete** act on the one shown. **Save** says which profile it writes into, for instance
*Save in "Everyday"*.

![The top of Settings: the profile Sharp airports in use, with Rename and Delete, above the presets](images/manual/profile-settings.png)

A profile keeps the look of the tiles: every question, the photo colours and the expert settings.
What belongs to the computer is the same for all of them: the X-Plane folder, the data folder,
SimBrief, the map data, the update check and the memory a build may take. Deleting a profile
deletes only its settings, never a tile.

### 3. Build with a profile

In step 1 of the **Plan**, **Settings profile** sets the build's imagery source, detail level and
every other setting. You can still change the source or the level for one build. When they no
longer match the profile's, a line says what the profile holds, and **Use them** takes them back.

![Step 1 of the Plan: the profile Sharp airports chosen, the level changed to ZL15, and the line "The profile Sharp airports is set to ZL17" with Use them](images/manual/profile-plan.png)

Works names the profile each build used.
