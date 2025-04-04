Components:

Data Backend:

- 2 Entities: ‘Workspace’ & ‘Item’  
- Simple way to create a workspace  
  - Must be no auth or very low auth  
  - Supports 3 types of access: admin, collaboration, viewing  
  - View access allows  
    - Reading all the items in a workspace  
  - Collaboration access allows  
    - Adding new items to a workspace  
    - Updating existing items in a workspace  
    - Getting notified on changes in the data  
    - Deleting items previously added by owner  
    - Updating personal profile  
  - Admin access allows  
    - Disabling/Enabling collaboration/view access on a workspace  
    - Updating workspace properties  
    - Deleting items in a workspace  
    - Deleting the entire workspace with its items.

Item Backend:

- Provides APIs to create an item in a workspace (for a set of predefined item kinds), e.g. Fake media, Photo, Audio, Object embed etc.  
- Validates the item before saving  
- Enriches the item (e.g. image processing for screenshots)  
- Provides a standard UI to edit media items and save them  
- Provides an ‘oembed-like’ API for embedding the items, getting them in svg format or png format etc.

Collaboration spaces:

- Individual apps, which get as an input the workspace collaboration access token  
- E.g.:  
  - Mapping interface for collaborating on a 2D map  
  - Chat-bot that auto-uploads screenshot items into a predefined workspace  
  - Admin interface, allowing an undecorated view of all items in a workspace, allowing edit, delete etc.  
- Each of these individual apps might modify items’ intrinsic properties, or add/modify app-specific metadata.  
- Multiple collaboration spaces can be used on a single workspace 

Viewing spaces:

- Individual apps, which get as an input the workspace viewing access token  
- Prodive an interesting view experience of items in a single workspace

# DB structure

Collection: <workspace>  
<workspace>/.config - contains apikeys and settings  
Each item in the collection is an item.

# API endpoints:

- POST /     + workspace metadata   
  Auth: none required  
  Create a new workspace, returns workspace-id and config  
  <workspace>/.config is created with the following structure:  
  - ./metadata: contains the user provided metadata  
  - ./keys: contains 3 api-keys (admin, collaborate, view), each a uuid4   
  - ./config: contains 2 keys: collaborate (default: False) and public (default: False)  
- POST /<workspace> + item metadata  
  Auth: (collab-key + collab on) or (admin-key)  
  Create a new item in the workspace, store in <workspace>/<item-id>  
  Item-id  is a uuid4  
  Structure for item is:  
  - ./key: item-key, a uuid4  
  - ./metadata: user provided metadata  
  returns item-id + item-key  
- GET /<workspace>  
  Auth: (view-key or public on) or (admin-key) or (collab-key)  
  Returns workspace metadata (i.e. the metadata field in the workspace .config)  
- GET /<workspace>/items  
  Auth: (view-key or public on) or (admin-key) or (collab-key)  
  Returns workspace items (i.e. a list of the metadata fields from items in the <workspace> collection.  
  Provide page query parameter for paging through the list  
- GET /<workspace>/<item-id>  
  Auth: (view-key or public on)  or (admin-key) or (collab-key)  
  Returns item metadata field  
- PUT /<workspace>/<item-id>  + item metadata  
  Auth: (collab-key + collab on + item-key) or (admin-key)  
  Update item’s metadata field  
- DELETE /<workspace>/<item-id>  
  Auth: (collab-key + collab on + item-key) or (admin-key)  
  Delete item from workspace  
- PUT /<workspace>  
  Auth: (admin-key)  
  Update metadata on a workspace  
  Also has optional public + collab query params to update these fields as well  
- DELETE /<workspace>  
  Auth: (admin-key)  
  Delete workspace and items.

