import json

class Artist(object):
	def __init__(self, name="", url="", picture="", summary="", content="", similar=""):
		self.name=name
		self.url=url
		self.picture=picture
		self.summary=summary
		self.content=content
		self.similar=similar

	def __unicode__(self):
		return self.name+ " "+ self.url+" "+self.picture

def parse_tag_artist(resp):
	jsd=json.loads(resp)
	if 'error' in jsd:
		return None
	artists_json=jsd['topartists']['artist']
	artists=[]
	for a in artists_json:
		artist=Artist(name=a['name'], url=a['url'], picture=a['image'][1]['#text'])
		artists.append(artist)
	return artists

def parse_artist_tags(resp):
	jsd=json.loads(resp)
	if 'error' in jsd:
		return None
	tags_json=jsd['toptags']['tag']
	tags=[]
	for t in tags_json:
		tags.append(t['name'])
	return tags

def parse_artist_info(resp):
	jsd=json.loads(resp)
	if 'error' in jsd:
		return None
	artist_json=jsd['artist']
	if 'similar' in artist_json:
		similar_artists=[a['name'] for a in artist_json['similar']['artist']]
	else:
		similar_artists=[]
	artist=Artist(name=artist_json['name'], content=artist_json['bio']['content'],
				  summary=artist_json['bio']['summary'], picture=artist_json['image'][len(artist_json['image'])-1]['#text'],
				  url=artist_json['url'], similar=similar_artists)
	return artist