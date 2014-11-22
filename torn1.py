import tornado.httpserver
import tornado.httpclient
import tornado.web
import tornado.ioloop
import tornado.options
import tornado.gen
import tornado.escape
import tornado.concurrent
import tornado.websocket
import re
import urllib


from string import Template
import os.path
#from pymongo import MongoClient
from motor import MotorClient
import json
from bson.json_util import dumps

import lfmparse

API_KEY = "YOUR_KEY_HERE"
GET_TAG = Template("http://ws.audioscrobbler.com/2.0/?method=tag.gettopartists&limit=500&tag=$tag&api_key=$api_key&format=json")
GET_ARTIST_TAGS=Template("http://ws.audioscrobbler.com/2.0/?method=artist.gettoptags&artist=$artist&api_key=$api_key&format=json")
GET_ARTIST_INFO=Template("http://ws.audioscrobbler.com/2.0/?method=artist.getinfo&artist=$artist&api_key=$api_key&format=json")

from tornado.options import define, options

define("port", default=8000)


class Application(tornado.web.Application):
	def __init__(self):
		self.client=MotorClient("localhost")
		self.db=self.client.lfm1
		self.done_current_fetch=True

		self.artist_status_subscribers=[]

		self.settings={
			"static_path": os.path.join(os.path.dirname(__file__), "static")
		}
		handlers = [(r"/static/(.*)",tornado.web.StaticFileHandler, {"path": "./static"},),
					(r"/", IndexHandler),
					(r"/artist/(\w+)", ArtistHandler),
					(r"/artists/", ArtistsHandler),
					(r"/tags/", TagsHandler),
					(r"/tag_json/", TagJsonHandler),
					(r"/multi_tag_json/", MultiTagJsonHandler),
					(r"/artist_json/", ArtistJsonHandler),
					(r"/artists_json/", ArtistsJsonHandler),
					(r"/tags_json/", TagsJsonHandler),
					(r"/ws_artist_queue", ArtistQueueWebsocket),
					(r"/search", SearchHandler)]
		template_path=os.path.join(os.path.dirname(__file__), 'templates')
		ui_modules={"ArtistListModule": ArtistListModule}
		tornado.web.Application.__init__(self, handlers, template_path=template_path, debug=True, **self.settings)

	@tornado.gen.coroutine
	def request_fetcher(self):
		if not self.done_current_fetch:
			return
		print("1 sec")
		artist_to_fetch=yield self.db.artists.find_one({"tags_cached": False})

		queued_artists=yield self.db.artists.find({"tags_cached": False}).limit(20).to_list(None)
		queued_artist_names=[a['name'] for a in queued_artists if a]
		if len(queued_artist_names)!=0:
			first_artist=queued_artist_names[0]
			yield self.get_artist(first_artist)
		for sub in self.artist_status_subscribers:
			try:
				sub.write_message(dumps(queued_artist_names))
			except tornado.websocket.WebSocketClosedError:
				self.artist_status_subscribers.remove(sub)
		self.done_current_fetch=True

	def start_request_fetcher(self):
		if options.port==8001:
			return
		self.fetcher=tornado.ioloop.PeriodicCallback(self.request_fetcher, 50000000, tornado.ioloop.IOLoop.instance())
		self.fetcher.start()
	@tornado.gen.coroutine
	def get_artists(self):
		artists=yield self.db.artists.find().to_list(None)
		return artists

	@tornado.gen.coroutine
	def get_tags(self):
		tags=yield self.db.tag_results.find().to_list(None)
		return tags

	@tornado.gen.coroutine
	def get_artists_by_names(self, names):
		artists=yield self.db.artists.find({"name": {"$in": names}}).to_list(None)
		return artists

	@tornado.gen.coroutine
	def get_artist(self, artist_name):
		artist=yield self.db.artists.find_one({"name": artist_name})
		if artist is None or not artist['tags_cached']:
			encoded_name=urllib.parse.quote(artist_name.encode('utf-8'))
			print("fetching"+encoded_name)
			client=tornado.httpclient.AsyncHTTPClient()
			response=yield client.fetch(GET_ARTIST_INFO.substitute(artist=encoded_name, api_key=API_KEY))
			art=lfmparse.parse_artist_info(response.body.decode())
			if art is None:
				raise tornado.get.Return(None)
			response=yield client.fetch(GET_ARTIST_TAGS.substitute(artist=encoded_name, api_key=API_KEY))
			art.tags=lfmparse.parse_artist_tags(response.body.decode())

			if artist is None:
				oid=yield self.db.artists.insert({"name": art.name, "summary": art.summary,
							"content": art.content, "url": art.url, "picture": art.picture,
							"tags": art.tags, "tags_cached": True, "similar": art.similar})
			else:
				artist['tags']=art.tags
				artist['summary']=art.summary
				artist['content']=art.content
				artist['url']=art.url
				artist['picture']=art.picture
				artist['similar']=art.similar
				artist['tags_cached']=True
				yield self.db.artists.save(artist)

			for tag in art.tags:
				cached_tag=yield self.db.tag_results.find_one({"name": tag})
				if cached_tag:
					cached_tag['artists']=list(set((cached_tag['artists'])+[art.name]))
					yield self.db.tag_results.save(cached_tag)
				else:
					yield self.db.tag_results.insert({"name": tag, "artists": [art.name], "fully_loaded:": False})
			artist=yield self.db.artists.find_one({"name": art.name})
		raise tornado.gen.Return(artist)


	@tornado.gen.coroutine
	def get_tag(self, tag_name):
		tag_entry=yield self.db.tag_results.find_one({"name": tag_name})
		if tag_entry is None or not tag_entry.get("fully_loaded"):
			encoded_tag=urllib.parse.quote(tag_name)
			client=tornado.httpclient.AsyncHTTPClient()
			response=yield client.fetch(GET_TAG.substitute(tag=encoded_tag, api_key=API_KEY))
			artists=lfmparse.parse_tag_artist(str(response.body.decode()))
			if artists is None:
				raise tornado.gen.Return(None)
			if tag_entry is None:
				yield self.db.tag_results.insert({"name": tag_name, "artists": [a.name for a in artists]})
			else:
				tag_entry["artists"]=list(set(tag_entry["artists"]+[a.name for a in artists]))
				tag_entry["fully_loaded"]=True
				yield self.db.tag_results.save(tag_entry)
			for a in artists:
				artist_res=yield self.db.artists.find_one({"name": a.name})
				if artist_res is None:
					print("ismeretlen eloado")
					yield self.db.artists.insert({"name": a.name, "url": a.url, "picture": a.picture, "tags": [tag_name], "tags_cached": False})
			tag_entry=yield self.db.tag_results.find_one({"name": tag_name})
		raise tornado.gen.Return(tag_entry)

	@tornado.gen.coroutine
	def get_multi_tag(self, tags):
		artist_names=[]
		for (i,tag_name) in enumerate(tags):
			tag=yield self.get_tag(tag_name)
			if tag is None:
				continue
			if i==0:
				artist_names=tag['artists']
			artist_names=list(set(tag["artists"]) & set(artist_names))
		artists=yield self.get_artists_by_names(artist_names)
		raise tornado.gen.Return(artists)

class ArtistListModule(tornado.web.UIModule):
	def render(self):
		return self.render_string("artist_list_module.html", artists=[])

class IndexHandler(tornado.web.RequestHandler):
	#@tornado.gen.coroutine
	# def get(self):
	# 	client = tornado.httpclient.AsyncHTTPClient()
	# 	response = yield client.fetch("http://index.hu")
	# 	print(response)
	# 	self.write(tornado.escape.xhtml_escape(response.body))

	def get(self):
		return self.redirect("/static/ang1.htm")

class MultiTagJsonHandler(tornado.web.RequestHandler):
	@tornado.gen.coroutine
	def get(self):
		tags=self.get_argument("q").split(",")
		if "" in tags:
			tags=tags.remove("")
		artists=yield self.application.get_multi_tag(tags)
		self.write(dumps(artists))
		self.finish()

class SearchHandler(tornado.web.RequestHandler):
	@tornado.gen.coroutine
	def get(self):
		tag_name=self.get_argument("q")
		tag=yield self.application.get_tag(tag_name)
		artist_names=tag['artists'] if tag is not None else []
		self.render("top_results.html", artist_names=artist_names)

class ArtistHandler(tornado.web.RequestHandler):
	@tornado.gen.coroutine
	def get(self, artist_name):
		artist=yield self.application.get_artist(artist_name)
		self.render("show_artist.html", artist=artist)


class ArtistsHandler(tornado.web.RequestHandler):
	def get(self):
		#artists=yield self.application.db.artists
		self.render("artist_list.html", artists=artists)

class TagsHandler(tornado.web.RequestHandler):
	def get(self):
		#tags=yield
		self.render("tag_list.html", tags=self.application.db.tag_results)

class TagJsonHandler(tornado.web.RequestHandler):
	@tornado.gen.coroutine
	def get(self):
		tag_name=self.get_argument("q")
		tag=yield self.application.get_tag(tag_name)
		self.write(dumps(tag))
		self.finish()

class ArtistJsonHandler(tornado.web.RequestHandler):
	@tornado.gen.coroutine
	def get(self):
		artist_name=self.get_argument("q")
		artist=yield self.application.get_artist(artist_name)
		self.write(dumps(artist))
		self.finish()

class ArtistsJsonHandler(tornado.web.RequestHandler):
	@tornado.gen.coroutine
	def get(self):
		artists=yield self.application.get_artists()
		arr=[a['name'] for a in artists]
		self.write(dumps(arr))
		self.finish()

class TagsJsonHandler(tornado.web.RequestHandler):
	@tornado.gen.coroutine
	def get(self):
		#arr=[a['name'] for a in self.application.get_tags().find()]
		#tags=[t for t in self.application.get_tags()]
		tags=yield self.application.get_tags()
		self.write(dumps(tags))
		self.finish()


class ArtistQueueWebsocket(tornado.websocket.WebSocketHandler):
	def check_origin(self, origin):
		return True

	def open(self):
		self.application.artist_status_subscribers.append(self)
		print("arist queue open")

	def close(self):
		self.application.artist_status_subscribers.remove(self)

	def on_message(self,message):
		pass
if __name__ == "__main__":
	application=Application()
	application.start_request_fetcher()
	tornado.options.parse_command_line()
	http_server = tornado.httpserver.HTTPServer(application)
	http_server.listen(options.port)
	tornado.ioloop.IOLoop.instance().start()